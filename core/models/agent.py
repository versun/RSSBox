import hashlib
import logging
import math
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from config import settings as app_settings
from openai import OpenAI
from openai.types.chat import ChatCompletion
from django.utils import timezone
from encrypted_model_fields.fields import EncryptedCharField
import time
import datetime
from django.core.cache import cache
from utils.text_handler import get_token_count, adaptive_chunking, chunk_html
import deepl
import json
from urllib import request, parse
from core.tasks.task_manager import task_manager
from core.tasks.utils import (
    TruncatedResponseError,
    auto_retry,
    classify_ai_error,
    is_timeout_error,
)

logger = logging.getLogger(__name__)

# Hard stops for recursive chunk completion, in case a chunk still exceeds the
# input budget after splitting.
MAX_CHUNK_RECURSION_DEPTH = 3
MAX_CHUNKS_PER_COMPLETION = 100
MIN_SPLIT_ON_TRUNCATE_TOKENS = 500
CHUNK_CACHE_VERSION = "v1"


class Agent(models.Model):
    name = models.CharField(_("Name"), max_length=100, unique=True)
    valid = models.BooleanField(_("Valid"), null=True)
    is_ai = models.BooleanField(default=False, editable=False)
    log = models.TextField(
        _("Log"),
        default="",
        blank=True,
        null=True,
    )

    def translate(self, text: str, target_language: str, **kwargs) -> dict:
        raise NotImplementedError(
            "subclasses of TranslatorEngine must provide a translate() method"
        )

    def min_size(self) -> int:
        if hasattr(self, "max_characters"):
            return self.max_characters * 0.7
        if hasattr(self, "max_tokens"):
            return self.max_tokens * 0.7
        return 0

    def max_size(self) -> int:
        if hasattr(self, "max_characters"):
            return self.max_characters * 0.9
        if hasattr(self, "max_tokens"):
            return self.max_tokens * 0.9
        return 0

    def validate(self) -> bool:
        raise NotImplementedError(
            "subclasses of TranslatorEngine must provide a validate() method"
        )

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


def openai_advanced_default():
    return {"temperature": 0.2,"reasoning_effort":"minimal"}


class OpenAIAgent(Agent):
    # https://platform.openai.com/docs/api-reference/chat
    is_ai = models.BooleanField(default=True, editable=False)
    api_key = EncryptedCharField(_("API Key"), max_length=255)
    base_url = models.URLField(_("API URL"), default="https://api.openai.com/v1")
    model = models.CharField(
        max_length=100,
        default="gpt-3.5-turbo",
        help_text="e.g. gpt-3.5-turbo, gpt-4-turbo",
    )
    title_translate_prompt = models.TextField(
        _("Title Translate Prompt"), default=app_settings.default_title_translate_prompt
    )
    content_translate_prompt = models.TextField(
        _("Content Translate Prompt"),
        default=app_settings.default_content_translate_prompt,
    )
    summary_prompt = models.TextField(default=app_settings.default_summary_prompt)

    advanced_params = models.JSONField(
        default=openai_advanced_default,
        help_text=("Advanced OpenAI chat params as JSON."),
    )
    custom_headers = models.JSONField(
        _("Custom HTTP Headers"),
        default=dict,
        blank=True,
        help_text=_(
            'Custom HTTP headers as a JSON object, e.g. {"X-Custom-Header": "value"}.'
        ),
    )
    max_tokens = models.IntegerField(
        default=0, help_text="0 means detect automatically"
    )
    max_chunk_tokens = models.IntegerField(
        _("Target Chunk Size (Tokens)"),
        default=4000,
        validators=[MinValueValidator(0)],
        help_text=_(
            "Target max input tokens per chunk for long articles "
            "(0 = use full model context window). Smaller chunks prevent timeouts."
        ),
    )
    request_timeout = models.FloatField(
        _("Request Timeout (s)"),
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Optional per-agent timeout override in seconds "
            "(leave blank to use OPENAI_API_TIMEOUT)."
        ),
    )
    rate_limit_rpm = models.IntegerField(
        _("Rate Limit (RPM)"),
        default=0,
        help_text=_("Maximum requests per minute (0 = no limit)"),
    )
    merge_system_prompt = models.BooleanField(
        _("Merge System Prompt to User Message"),
        default=False,
        help_text=_("Enable for models that don't support system system instructions (e.g., Gemma 3)")
    )
    EXTRA_HEADERS = {
        "HTTP-Referer": "https://www.rssbox.app",
        "X-Title": "RSSBox",
    }

    class Meta:
        verbose_name = "OpenAI"
        verbose_name_plural = "OpenAI"

    def clean(self):
        super().clean()
        errors = {}
        if self.max_chunk_tokens is not None and self.max_chunk_tokens < 0:
            errors["max_chunk_tokens"] = _(
                "Target chunk size must be 0 or greater."
            )
        if self.request_timeout is not None and (
            not math.isfinite(self.request_timeout) or self.request_timeout <= 0
        ):
            errors["request_timeout"] = _(
                "Request timeout must be a finite number greater than 0."
            )
        if self.custom_headers is None:
            self.custom_headers = {}
        if not isinstance(self.custom_headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.custom_headers.items()
        ):
            errors["custom_headers"] = _(
                "Custom HTTP headers must be a JSON object with string values."
            )
        if errors:
            raise ValidationError(errors)

    def _get_request_headers(self):
        return {**self.EXTRA_HEADERS, **(self.custom_headers or {})}

    def effective_chunk_limit(self) -> int:
        """Per-call input token budget bounded by both max_tokens and max_chunk_tokens."""
        if self.max_tokens <= 0:
            return 0
        if self.max_chunk_tokens and self.max_chunk_tokens > 0:
            return min(self.max_tokens, self.max_chunk_tokens)
        return self.max_tokens

    def min_size(self) -> int:
        return self.effective_chunk_limit() * 0.7

    def max_size(self) -> int:
        return self.effective_chunk_limit() * 0.9

    def _init(self):
        timeout = (
            self.request_timeout
            if self.request_timeout is not None
            and math.isfinite(self.request_timeout)
            and self.request_timeout > 0
            else settings.OPENAI_API_TIMEOUT
        )
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=settings.OPENAI_API_MAX_RETRIES,
        )

    def validate(self) -> bool:
        if self.api_key:
            try:
                client = self._init()
                # 应用速率限制
                self._wait_for_rate_limit()

                system_prompt = "You must only reply with exactly one character: 1"
                user_content = "1"
                
                # 根据 merge_system_prompt 的值决定消息格式
                if self.merge_system_prompt:
                    merged_content = f"{system_prompt}\n\n{user_content}"
                    messages = [
                        {"role": "user", "content": merged_content}
                    ]
                else:
                    messages = [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {"role": "user", "content": user_content},
                    ]

                res = client.with_options(
                    max_retries=settings.OPENAI_API_MAX_RETRIES
                ).chat.completions.create(
                    extra_headers=self._get_request_headers(),
                    model=self.model,
                    messages=messages,
                    # max_tokens=50,
                    max_completion_tokens=50,
                )
                # 有些第三方源在key或url错误的情况下，并不会抛出异常代码，而是返回html广告，因此添加该行。
                fr = res.choices[0].finish_reason
                # 提交后台任务检测模型限制
                if self.max_tokens == 0:
                    task_manager.submit_task(
                        f"detect_model_limit_{self.model}_{self.id}",
                        self.detect_model_limit,
                        force=True,
                    )
                    logger.info(
                        f"Submitted background task to detect model limit for {self.model}"
                    )
                self.log = ""
                self.valid = True
                return True
            except Exception as e:
                logger.error("OpenAIAgent validate ->%s", e)
                self.log = f"{timezone.now()}: {str(e)}"
                self.valid = False
                return False
            finally:
                self.save(update_fields=["log", "valid"])

    def detect_model_limit(self, force=False) -> int:
        """通过二分搜索来高效检测模型实际限制"""
        if not force and self.max_tokens != 0:
            return self.max_tokens

        initial_model = self.model
        initial_max_tokens = self.max_tokens

        # 二分搜索找到确切限制
        def binary_search_limit(low, high):
            """使用二分搜索找到确切的token限制"""
            if high - low <= 256:  # 当范围足够小时，返回低值作为安全限制
                return low

            mid = (low + high) // 2

            try:
                # 应用速率限制
                self._wait_for_rate_limit()
                # 使用最小的测试内容减少token消耗
                response = self._init().chat.completions.create(
                    extra_headers=self._get_request_headers(),
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You must only reply with exactly one character: 1",
                        },
                        {"role": "user", "content": "1"},
                    ],
                    # max_tokens=mid,
                    max_completion_tokens=mid,
                    temperature=0,  # 确保结果一致性
                    stop=[",", "\n", " ", ".", "1"],
                )
                if response.choices[0].finish_reason == "stop":
                    # 成功调用，尝试更高的限制
                    return binary_search_limit(mid, high)

            except Exception as e:
                error_str = str(e).lower()
                if any(
                    keyword in error_str
                    for keyword in ["maximum", "limit", "tokens", "context", "length"]
                ):
                    # 遇到限制错误，降低上限
                    return binary_search_limit(low, mid)
                else:
                    # 其他错误（如API错误），使用保守值
                    logger.warning(
                        f"Detect model limit when non-limit error occurs: {e}"
                    )
                    return low

        # 直接使用二分搜索
        final_limit = binary_search_limit(4096, 1000000)
        self.max_tokens = final_limit

        if self.pk is None:
            return final_limit

        updated = type(self).objects.filter(
            pk=self.pk,
            model=initial_model,
            max_tokens=initial_max_tokens,
        ).update(max_tokens=final_limit)
        if updated:
            return final_limit

        current_max_tokens = (
            type(self).objects.filter(pk=self.pk).values_list("max_tokens", flat=True).first()
        )
        if current_max_tokens is not None:
            self.max_tokens = current_max_tokens
            return current_max_tokens

        return final_limit

    def _wait_for_rate_limit(self):
        """等待直到满足速率限制条件"""
        if self.rate_limit_rpm <= 0:
            return  # 无速率限制

        # 生成基于当前分钟的缓存键
        current_minute = datetime.datetime.now().strftime("%Y%m%d%H%M")
        cache_key = f"openai_rate_limit_{self.id}_{current_minute}"

        # 获取当前计数或初始化为0
        request_count = cache.get(cache_key, 0)

        # 计算等待时间（如果超过限制）
        if request_count >= self.rate_limit_rpm:
            # 计算到下一分钟开始的时间
            now = datetime.datetime.now()
            next_minute = now.replace(second=0, microsecond=0) + datetime.timedelta(
                minutes=1
            )
            wait_seconds = (next_minute - now).total_seconds()

            # 添加一点缓冲确保时间窗口切换
            wait_seconds += 0.1
            logger.info(f"Rate limit reached. Waiting {wait_seconds:.2f} seconds...")
            time.sleep(wait_seconds)

            # 重置计数（新分钟开始）
            cache.delete(cache_key)
            return

        # 增加计数并设置过期时间（确保在下一分钟开始时过期）
        cache.set(cache_key, request_count + 1, timeout=60)

    def _build_chunk_cache_key(
        self, chunk_text: str, system_prompt: str, chunk_cache_context: dict
    ) -> str:
        adv_params = (
            self.advanced_params if isinstance(self.advanced_params, dict) else {}
        )
        payload = json.dumps(
            {
                "v": CHUNK_CACHE_VERSION,
                "entry_id": chunk_cache_context.get("entry_id"),
                "agent_id": self.pk,
                "model": self.model,
                "base_url": self.base_url,
                "stage": chunk_cache_context.get("stage", "content"),
                "target_language": chunk_cache_context.get("target_language", ""),
                "system_prompt": system_prompt or "",
                "merge_system_prompt": bool(self.merge_system_prompt),
                "advanced_params": adv_params,
                "custom_headers": self.custom_headers,
                "chunk": chunk_text,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get_cached_chunk(self, cache_key: str) -> tuple[str, int] | None:
        """Return ``(translated_text, unbilled_tokens)`` if a fresh cache row exists."""
        from core.models.entry import TranslationChunkCache

        try:
            row = TranslationChunkCache.objects.filter(cache_key=cache_key).first()
            if row is None:
                return None
            ttl = getattr(settings, "CHUNK_CACHE_TTL_SECONDS", 60 * 60 * 72)
            cutoff = timezone.now() - datetime.timedelta(seconds=ttl)
            if row.created_at < cutoff:
                row.delete()
                return None
            return row.translated_text, max(0, int(row.tokens or 0))
        except Exception:
            logger.warning("Chunk cache lookup failed for key %s", cache_key[:12])
            return None

    def _save_cached_chunk(
        self,
        cache_key: str,
        translated_text: str,
        tokens: int,
        chunk_cache_context: dict,
    ) -> None:
        from core.models.entry import TranslationChunkCache

        try:
            TranslationChunkCache.objects.update_or_create(
                cache_key=cache_key,
                defaults={
                    "entry_id": chunk_cache_context.get("entry_id"),
                    "stage": chunk_cache_context.get("stage", "content"),
                    "translated_text": translated_text,
                    "tokens": max(0, int(tokens or 0)),
                    "created_at": timezone.now(),
                },
            )
        except Exception:
            logger.warning("Chunk cache save failed for key %s", cache_key[:12])

    def _compute_output_token_limit(
        self, input_tokens: int, text_token_count: int
    ) -> int:
        """Compute a safe default max_completion_tokens bounded by remaining
        context headroom (so input + output never exceeds max_tokens), input
        scale, and OPENAI_MAX_OUTPUT_TOKENS so large-context models (e.g. 1M
        context) do not unlock runaway reasoning/output budgets."""
        remaining = max(1, self.max_tokens - input_tokens)
        context_headroom = min(remaining, max(256, int(remaining * 0.8)))
        configured_cap = max(
            256, getattr(settings, "OPENAI_MAX_OUTPUT_TOKENS", 16384)
        )
        estimated_needed = max(4096, int(text_token_count * 4))
        return min(context_headroom, configured_cap, estimated_needed)

    def _split_text_for_budget(
        self, text: str, text_token_count: int, budget: int, *, is_html: bool
    ) -> list[str]:
        if is_html:
            return chunk_html(text, max_tokens=budget)
        # min_chunk_size 不能超过可用预算，否则分块结果仍会超过预算
        return adaptive_chunking(
            text,
            target_chunks=max(2, math.ceil(text_token_count / budget)),
            min_chunk_size=min(500, budget),
            max_chunk_size=budget,
        )

    def _complete_chunks(
        self,
        chunks: list[str],
        *,
        system_prompt: str,
        chunk_depth: int,
        is_html: bool,
        chunk_cache_context: dict | None,
        initial_tokens: int = 0,
        **kwargs,
    ) -> tuple[str, int, list[str]]:
        if len(chunks) > MAX_CHUNKS_PER_COMPLETION:
            raise RuntimeError(
                f"Text produced {len(chunks)} chunks, above the limit of "
                f"{MAX_CHUNKS_PER_COMPLETION}; refusing to call the provider"
            )

        # Strip cache_leaf for child chunks since this loop persists each chunk
        # directly and handles double-entry token accounting across retries.
        child_cache_context = (
            {k: v for k, v in chunk_cache_context.items() if k != "cache_leaf"}
            if chunk_cache_context
            else None
        )
        tokens = initial_tokens
        billed_cache_keys: list[str] = []
        translated_chunks = []
        total_chunks = len(chunks)

        for chunk_idx, chunk in enumerate(chunks, start=1):
            cache_key = None
            if child_cache_context:
                cache_key = self._build_chunk_cache_key(
                    chunk, system_prompt, child_cache_context
                )
                cached = self._get_cached_chunk(cache_key)
                if cached is not None:
                    cached_text, unbilled_tokens = cached
                    logger.info(
                        "Reusing cached chunk %s/%s for entry=%s stage=%s",
                        chunk_idx,
                        total_chunks,
                        child_cache_context.get("entry_id"),
                        child_cache_context.get("stage"),
                    )
                    translated_chunks.append(cached_text)
                    if unbilled_tokens:
                        tokens += unbilled_tokens
                        billed_cache_keys.append(cache_key)
                    continue

            try:
                # user_prompt 已追加到 system_prompt，递归时不要重复追加
                result = auto_retry(
                    self.completions,
                    max_retries=2,
                    text=chunk,
                    system_prompt=system_prompt,
                    _chunk_depth=chunk_depth + 1,
                    is_html=is_html,
                    chunk_cache_context=child_cache_context,
                    **kwargs,
                )
            except Exception as chunk_error:
                # auto_retry attaches what the failed attempts paid for
                tokens += getattr(chunk_error, "tokens_used", 0)
                billed_cache_keys.extend(
                    getattr(chunk_error, "billed_cache_keys", ()) or ()
                )
                if tokens:
                    chunk_error.tokens_used = tokens
                if billed_cache_keys:
                    chunk_error.billed_cache_keys = tuple(
                        dict.fromkeys(billed_cache_keys)
                    )
                chunk_error.is_chunked_failure = True
                raise

            translated_chunks.append(result["text"])
            chunk_tokens = result.get("tokens", 0)
            tokens += chunk_tokens
            if result.get("billed_cache_keys"):
                billed_cache_keys.extend(result["billed_cache_keys"])
            if cache_key and child_cache_context:
                self._save_cached_chunk(
                    cache_key,
                    result["text"],
                    chunk_tokens,
                    child_cache_context,
                )
                billed_cache_keys.append(cache_key)

        joiner = "\n" if is_html else " "
        return (
            joiner.join(translated_chunks),
            tokens,
            list(dict.fromkeys(billed_cache_keys)),
        )

    def completions(
        self,
        text: str,
        system_prompt: str = None,
        user_prompt: str = None,
        _chunk_depth: int = 0,  # 内部参数：分块递归深度，>0 表示分块调用
        is_html: bool = False,
        chunk_cache_context: dict = None,
        **kwargs,
    ) -> dict:
        tokens = 0
        result_text = ""
        leaf_input_tokens = 0
        billed_cache_keys: list[str] = []
        provider_call_started_at = None

        try:
            system_prompt = system_prompt or ""
            if user_prompt:
                system_prompt = f"{system_prompt}\n\n{user_prompt}"

            if self.max_tokens == 0:
                task_manager.submit_task(
                    f"detect_model_limit_{self.model}_{self.id}",
                    self.detect_model_limit,
                    force=True,
                )
                raise ValueError(
                    "max_tokens is not set, Please wait for the model limit detection to complete"
                )

            # 无论是否合并，system_prompt 都占用输入空间（保留 100 token buffer）
            prompt_tokens = get_token_count(system_prompt)
            text_token_count = get_token_count(text)
            max_usable_tokens = self.max_tokens - prompt_tokens - 100
            if max_usable_tokens <= 0:
                raise ValueError(
                    f"No usable input tokens left (max_tokens={self.max_tokens}, "
                    f"prompt_tokens={prompt_tokens}, buffer=100); "
                    "increase max_tokens or shorten the prompt"
                )

            # 将目标分块大小与模型总上下文容量解耦：大上下文模型对长文也按目标块大小切分，
            # 避免单次非流式请求过长导致超时和重复计费（issue #186）。
            effective_chunk_budget = max_usable_tokens
            if self.max_chunk_tokens and self.max_chunk_tokens > 0:
                effective_chunk_budget = min(max_usable_tokens, self.max_chunk_tokens)

            # 检查文本长度是否需要分块
            if text_token_count > effective_chunk_budget:
                if _chunk_depth >= MAX_CHUNK_RECURSION_DEPTH:
                    raise RuntimeError(
                        "Chunk recursion depth exceeded "
                        f"({MAX_CHUNK_RECURSION_DEPTH}); refusing to split further"
                    )

                logger.info(
                    "Text too large (%s tokens > budget %s), chunking (is_html=%s)...",
                    text_token_count,
                    effective_chunk_budget,
                    is_html,
                )

                chunks = self._split_text_for_budget(
                    text, text_token_count, effective_chunk_budget, is_html=is_html
                )

                # If an atomic HTML node (e.g. an oversized <img>) cannot be
                # split any smaller, proceed directly as a leaf call when it
                # still fits inside the model's hard usable window; otherwise
                # fail fast instead of recursing uselessly.
                atomic_unsplit = (
                    len(chunks) == 1
                    and get_token_count(chunks[0]) >= text_token_count
                )
                if atomic_unsplit:
                    if text_token_count > max_usable_tokens:
                        raise RuntimeError(
                            f"Atomic chunk ({text_token_count} tokens) exceeds "
                            f"usable input budget ({max_usable_tokens} tokens)"
                        )
                else:
                    joined_text, total_tokens, child_keys = self._complete_chunks(
                        chunks,
                        system_prompt=system_prompt,
                        chunk_depth=_chunk_depth,
                        is_html=is_html,
                        chunk_cache_context=chunk_cache_context,
                        **kwargs,
                    )
                    res_dict = {"text": joined_text, "tokens": total_tokens}
                    if child_keys:
                        res_dict["billed_cache_keys"] = child_keys
                    return res_dict

            leaf_cache_key = None
            if (
                chunk_cache_context
                and chunk_cache_context.get("cache_leaf")
                and _chunk_depth == 0
            ):
                leaf_cache_key = self._build_chunk_cache_key(
                    text, system_prompt, chunk_cache_context
                )
                cached = self._get_cached_chunk(leaf_cache_key)
                if cached is not None:
                    cached_text, unbilled_tokens = cached
                    logger.info(
                        "Reusing cached leaf call for entry=%s stage=%s",
                        chunk_cache_context.get("entry_id"),
                        chunk_cache_context.get("stage"),
                    )
                    res_dict = {"text": cached_text, "tokens": unbilled_tokens}
                    if unbilled_tokens:
                        res_dict["billed_cache_keys"] = [leaf_cache_key]
                    return res_dict

            if self.merge_system_prompt:
                messages = [{"role": "user", "content": f"{system_prompt}\n\n{text}"}]
            else:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ]
            input_tokens = prompt_tokens + text_token_count
            leaf_input_tokens = input_tokens

            # 只在真正请求服务商前初始化客户端并占用速率配额
            client = self._init()
            self._wait_for_rate_limit()

            output_token_limit = self._compute_output_token_limit(
                input_tokens=input_tokens, text_token_count=text_token_count
            )

            # 正常流程
            adv_params = self.advanced_params or {}
            if not isinstance(adv_params, dict):
                adv_params = {}

            call_kwargs = {**adv_params}
            # 仅在未显式提供时设置安全的默认/限制
            if (
                "max_completion_tokens" not in call_kwargs
                and "max_tokens" not in call_kwargs
            ):
                call_kwargs["max_completion_tokens"] = output_token_limit

            provider_call_started_at = time.monotonic()
            request_client = client.with_options(
                max_retries=settings.OPENAI_API_MAX_RETRIES
            )
            if "reasoning" in call_kwargs:
                res = request_client.with_options(
                    default_headers=self._get_request_headers()
                ).post(
                    "/chat/completions",
                    body={
                        "model": self.model,
                        "messages": messages,
                        **call_kwargs,
                    },
                    cast_to=ChatCompletion,
                )
            else:
                res = request_client.chat.completions.create(
                    extra_headers=self._get_request_headers(),
                    model=self.model,
                    messages=messages,
                    **call_kwargs,
                )
            elapsed = time.monotonic() - provider_call_started_at
            provider_call_started_at = None

            # 先记录用量：即使响应不完整，这次调用也已计费
            usage = getattr(res, "usage", None)
            tokens = getattr(usage, "total_tokens", 0) if usage else 0
            prompt_tok = getattr(usage, "prompt_tokens", None) if usage else None
            completion_tok = (
                getattr(usage, "completion_tokens", None) if usage else None
            )
            details = (
                getattr(usage, "completion_tokens_details", None) if usage else None
            )
            reasoning_tok = (
                getattr(details, "reasoning_tokens", None) if details else None
            )
            request_id = getattr(res, "id", None)

            finish_reason = None
            if res.choices:
                try:
                    finish_reason = res.choices[0].finish_reason
                except Exception:
                    finish_reason = None

            logger.info(
                "[%s] model=%s entry=%s stage=%s attempt_id=%s request_id=%s "
                "finish_reason=%s elapsed=%.2fs tokens=%s "
                "(prompt=%s completion=%s reasoning=%s)",
                self.name,
                self.model,
                chunk_cache_context.get("entry_id") if chunk_cache_context else None,
                chunk_cache_context.get("stage") if chunk_cache_context else None,
                chunk_cache_context.get("attempt_id") if chunk_cache_context else None,
                request_id,
                finish_reason,
                elapsed,
                tokens,
                prompt_tok,
                completion_tok,
                reasoning_tok,
            )

            content = (
                getattr(getattr(res.choices[0], "message", None), "content", None)
                if res.choices
                else None
            )
            if finish_reason == "stop" and content and content.strip():
                result_text = content
                logger.debug(f"[{self.name}]: {result_text[:50]}...")
            elif finish_reason == "length":
                # Adaptive recovery: if this input is still large enough to
                # halve, split into smaller chunks so output stays under the
                # token cap instead of deterministically truncating again.
                if (
                    text_token_count >= MIN_SPLIT_ON_TRUNCATE_TOKENS
                    and _chunk_depth < MAX_CHUNK_RECURSION_DEPTH
                ):
                    half_budget = max(250, text_token_count // 2)
                    sub_chunks = self._split_text_for_budget(
                        text, text_token_count, half_budget, is_html=is_html
                    )
                    if 2 <= len(sub_chunks) <= MAX_CHUNKS_PER_COMPLETION:
                        logger.warning(
                            "[%s] Output truncated (finish_reason=length) for %s "
                            "tokens; adaptively splitting into %s chunks (budget=%s)",
                            self.name,
                            text_token_count,
                            len(sub_chunks),
                            half_budget,
                        )
                        joined_text, total_tokens, child_keys = self._complete_chunks(
                            sub_chunks,
                            system_prompt=system_prompt,
                            chunk_depth=_chunk_depth,
                            is_html=is_html,
                            chunk_cache_context=chunk_cache_context,
                            initial_tokens=tokens,
                            **kwargs,
                        )
                        if leaf_cache_key and chunk_cache_context:
                            self._save_cached_chunk(
                                leaf_cache_key,
                                joined_text,
                                total_tokens,
                                chunk_cache_context,
                            )
                            child_keys = list(
                                dict.fromkeys([*child_keys, leaf_cache_key])
                            )
                        res_dict = {"text": joined_text, "tokens": total_tokens}
                        if child_keys:
                            res_dict["billed_cache_keys"] = child_keys
                        return res_dict
                raise TruncatedResponseError(
                    "No complete text response (finish_reason=length)"
                )
            else:
                raise RuntimeError(
                    f"No complete text response (finish_reason={finish_reason or 'unknown'})"
                )

            if leaf_cache_key and chunk_cache_context:
                self._save_cached_chunk(
                    leaf_cache_key, result_text, tokens, chunk_cache_context
                )
                billed_cache_keys.append(leaf_cache_key)
        except Exception as e:
            if provider_call_started_at is not None:
                failed_elapsed = time.monotonic() - provider_call_started_at
                logger.warning(
                    "[%s] model=%s entry=%s stage=%s attempt_id=%s error_type=%s "
                    "elapsed=%.2fs est_input_tokens=%s: %s",
                    self.name,
                    self.model,
                    chunk_cache_context.get("entry_id")
                    if chunk_cache_context
                    else None,
                    chunk_cache_context.get("stage") if chunk_cache_context else None,
                    chunk_cache_context.get("attempt_id")
                    if chunk_cache_context
                    else None,
                    classify_ai_error(e),
                    failed_elapsed,
                    leaf_input_tokens,
                    e,
                )
            # Attach the spent usage so the caller can account for the failed
            # attempt instead of losing the tokens it already paid for.
            if tokens and not getattr(e, "is_chunked_failure", False):
                e.tokens_used = tokens
            if billed_cache_keys:
                e.billed_cache_keys = tuple(
                    dict.fromkeys(
                        list(getattr(e, "billed_cache_keys", ()) or ())
                        + billed_cache_keys
                    )
                )
            if (
                leaf_input_tokens
                and is_timeout_error(e)
                and not getattr(e, "estimated_unbilled_tokens", 0)
            ):
                e.estimated_unbilled_tokens = leaf_input_tokens
            # 分块调用的异常交给最外层统一记录，避免每层递归重复记录
            if _chunk_depth == 0:
                self.log = f"{timezone.now()}: {str(e)}"
                logger.error(f"{self.name}: {e}")
                self.save(update_fields=["log"])
            raise

        res_dict = {"text": result_text, "tokens": tokens}
        if billed_cache_keys:
            res_dict["billed_cache_keys"] = billed_cache_keys
        return res_dict

    def translate(
        self,
        text: str,
        target_language: str,
        user_prompt: str = None,
        text_type: str = "title",
        **kwargs,
    ) -> dict:
        logger.info(f">>>Start Translate [{target_language}]: {text[:50]}...")
        system_prompt = (
            self.title_translate_prompt
            if text_type == "title"
            else self.content_translate_prompt
        ).replace("{target_language}", target_language)

        if text_type == "content" and "is_html" not in kwargs:
            kwargs["is_html"] = True

        return self.completions(
            text, system_prompt=system_prompt, user_prompt=user_prompt, **kwargs
        )

    def summarize(self, text: str, target_language: str, **kwargs) -> dict:
        logger.info(f">>> Start Summarize [{target_language}]: {text[:50]}...")
        system_prompt = self.summary_prompt.replace(
            "{target_language}", target_language
        )
        return self.completions(text, system_prompt=system_prompt, **kwargs)

    def digester(
        self,
        text: str,
        system_prompt: str,
        digest_name: str,
        date: str,
        **kwargs,
    ) -> dict:
        logger.info(f">>> Start Digesting [{digest_name}]-{date}")
        # prompt = system_prompt + settings.output_format_for_digest_prompt
        return self.completions(text, system_prompt=system_prompt, **kwargs)

    def filter(self, text: str, system_prompt: str, **kwargs) -> dict:
        logger.info(f">>> Start Filter: {text[:50]}...")
        results = self.completions(
            text,
            system_prompt=system_prompt + app_settings.output_format_for_filter_prompt,
            **kwargs,
        )

        passed = "passed" in (results["text"] or "").lower()
        logger.info(">>> Filter Passed" if passed else ">>> Filter Blocked")
        # 无论通过与否，这次调用都已计费
        return {"passed": passed, "tokens": results.get("tokens", 0)}


class DeepLAgent(Agent):
    # https://github.com/DeepLcom/deepl-python
    api_key = EncryptedCharField(_("API Key"), max_length=255)
    max_characters = models.IntegerField(default=5000)
    server_url = models.URLField(_("API URL(optional)"), null=True, blank=True)
    proxy = models.URLField(_("Proxy(optional)"), null=True, blank=True)
    language_code_map = {
        "English": "EN-US",
        "Chinese Simplified": "ZH",
        "Russian": "RU",
        "Japanese": "JA",
        "Korean": "KO",
        "Czech": "CS",
        "Danish": "DA",
        "German": "DE",
        "Spanish": "ES",
        "French": "FR",
        "Indonesian": "ID",
        "Italian": "IT",
        "Hungarian": "HU",
        "Norwegian Bokmål": "NB",
        "Dutch": "NL",
        "Polish": "PL",
        "Portuguese": "PT-PT",
        "Swedish": "SV",
        "Turkish": "TR",
    }

    class Meta:
        verbose_name = "DeepL"
        verbose_name_plural = "DeepL"

    def _init(self):
        return deepl.Translator(
            self.api_key, server_url=self.server_url, proxy=self.proxy
        )

    def validate(self) -> bool:
        is_valid = False
        try:
            translator = self._init()
            usage = translator.get_usage()
            if usage.character.valid:
                self.log = ""
                is_valid = True
        except Exception as e:
            logger.error("DeepLTranslator validate ->%s", e)
            self.log = f"{timezone.now()}: {str(e)}"
            is_valid = False
        finally:
            self.valid = is_valid
            self.save()
        return is_valid

    def translate(self, text: str, target_language: str, **kwargs) -> dict:
        logger.info(">>> DeepL Translate [%s]: %s", target_language, text)
        target_code = self.language_code_map.get(target_language, None)
        translated_text = ""
        try:
            if target_code is None:
                logger.error(
                    "DeepLTranslator->Not support target language:%s", target_language
                )
            translator = self._init()
            resp = translator.translate_text(
                text,
                target_lang=target_code,
                preserve_formatting=True,
                split_sentences="nonewlines",
                tag_handling="html",
            )
            translated_text = resp.text
        except Exception as e:
            logger.error("DeepLTranslator->%s: %s", e, text)
            self.log = f"{timezone.now()}: {str(e)}"
        finally:
            self.save()
        return {"text": translated_text, "characters": len(text)}


class LibreTranslateAgent(Agent):
    """
    An Agent that uses a LibreTranslate server for translation,
    with API communication logic integrated directly into the class.
    """

    api_key = EncryptedCharField(_("API Key (if required)"), max_length=255, blank=True)
    server_url = models.URLField(
        verbose_name="Server URL",
        default="https://libretranslate.com",
        help_text="Your self-hosted or public LibreTranslate server endpoint",
    )
    max_characters = models.IntegerField(
        default=5000,
        verbose_name="Max Characters",
        help_text="Maximum characters per translation request",
    )
    language_map = {
        "Chinese Simplified": "zh",
        "Chinese Traditional": "zh",
        "English": "en",
        "Spanish": "es",
        "French": "fr",
        "German": "de",
        "Italian": "it",
        "Portuguese": "pt",
        "Russian": "ru",
        "Japanese": "ja",
        "Dutch": "nl",
        "Korean": "ko",
        "Czech": "cs",
        "Danish": "da",
        "Indonesian": "id",
        "Polish": "pl",
        "Hungarian": "hu",
        "Norwegian Bokmål": "nb",
        "Swedish": "sv",
        "Turkish": "tr",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    # --------------------------------
    # API Methods
    # --------------------------------
    def _api_request(
        self, endpoint: str, params: dict = None, method: str = "POST"
    ) -> any:
        """
        Handles sending requests to the configured LibreTranslate server endpoint.
        """
        try:
            url = self.server_url
            if not url.endswith("/"):
                url += "/"
            full_url = f"{url}{endpoint}"

            query_params = params or {}
            if self.api_key:
                query_params["api_key"] = self.api_key

            data = parse.urlencode(query_params).encode("utf-8")
            req = request.Request(full_url, data=data, method=method)
            req.add_header("accept", "application/json")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            req.add_header("User-Agent", "LibreTranslateAgent/1.0")

            with request.urlopen(req, timeout=settings.LT_TIMEOUT) as response:
                response_str = response.read().decode("utf-8")
                return json.loads(response_str)
        except Exception as e:
            raise ConnectionError(f"_api_request {str(e)}")  # e.reason

    def _api_translate(
        self, q: str, source: str, target: str, format: str = "html"
    ) -> str:
        """Calls the /translate endpoint."""
        params = {"q": q, "source": source, "target": target, "format": format}
        response_data = self._api_request("translate", params=params, method="POST")

        if "error" in response_data:
            raise Exception(f"_api_translate Error: {response_data['error']}")

        return response_data.get("translatedText", "")

    def _api_languages(self) -> list:
        """Calls the /languages endpoint."""
        # Languages endpoint requires a GET request
        return self._api_request("languages", method="GET")

    # --------------------------------
    # Agent Methods
    # --------------------------------
    def validate(self) -> bool:
        is_valid = False
        try:
            self._api_languages()
            self.log = ""
            is_valid = True
        except Exception as e:
            self.log = f"{timezone.now()}: {str(e)}"
            is_valid = False
        finally:
            self.valid = is_valid
            self.save()
        return is_valid

    def translate(self, text: str, target_language: str, **kwargs) -> dict:
        target_code = self.language_map.get(target_language)
        if not target_code:
            self.log += (
                f"{timezone.now()}: Not support target language: {target_language}"
            )
            logger.error(
                f"LibreTranslateAgent->Not support target language: {target_language}"
            )
            self.save()
            return {"text": "", "characters": 0}

        try:
            translated_text = self._api_translate(
                q=text, source="auto", target=target_code, format="html"
            )
            return {"text": translated_text, "characters": len(text)}
        except Exception as e:
            logger.error("LibreTranslateAgent->: %s", str(e))
            self.log = f"{timezone.now()}: {str(e)}"
            self.save()
            return {"text": "", "characters": 0}

    class Meta:
        verbose_name = "LibreTranslate"
        verbose_name_plural = "LibreTranslate"


class TestAgent(Agent):
    translated_text = models.TextField(default="@@Translated Text@@")
    max_characters = models.IntegerField(default=50000)
    max_tokens = models.IntegerField(default=50000)
    interval = models.IntegerField(_("Request Interval(s)"), default=3)
    is_ai = models.BooleanField(default=True, editable=False)

    class Meta:
        verbose_name = "Test"
        verbose_name_plural = "Test"

    def validate(self) -> bool:
        return True

    def translate(self, text: str, target_language: str, **kwargs) -> dict:
        logger.info(">>> Test Translate [%s]: %s", target_language, text)
        time.sleep(self.interval)
        return {"text": self.translated_text, "tokens": 10, "characters": len(text)}

    def summarize(self, text: str, target_language: str, **kwargs) -> dict:
        logger.info(">>> Test Summarize [%s]: %s", target_language, text)
        time.sleep(self.interval)
        return {"text": self.translated_text, "tokens": 10, "characters": len(text)}

    def filter(self, text: str, **kwargs):
        logger.info(">>> Test Filter")
        import random

        time.sleep(self.interval)
        return {"passed": random.choice([True, False]), "tokens": 10}
