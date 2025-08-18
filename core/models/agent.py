import logging
from django.db import models
from django.utils.translation import gettext_lazy as _
from config import settings
from openai import OpenAI
from django.utils import timezone
from encrypted_model_fields.fields import EncryptedCharField
import time
import datetime
from django.core.cache import cache
from utils.text_handler import get_token_count, adaptive_chunking
import deepl
import json
from urllib import request, parse
from utils.task_manager import task_manager

logger = logging.getLogger(__name__)


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
        _("Title Translate Prompt"), default=settings.default_title_translate_prompt
    )
    content_translate_prompt = models.TextField(
        _("Content Translate Prompt"), default=settings.default_content_translate_prompt
    )
    summary_prompt = models.TextField(default=settings.default_summary_prompt)

    temperature = models.FloatField(default=0.2)
    top_p = models.FloatField(default=0.2)
    frequency_penalty = models.FloatField(default=0)
    presence_penalty = models.FloatField(default=0)
    max_tokens = models.IntegerField(default=0)
    rate_limit_rpm = models.IntegerField(
        _("Rate Limit (RPM)"),
        default=0,
        help_text=_("Maximum requests per minute (0 = no limit)"),
    )
    EXTRA_HEADERS = {
        "HTTP-Referer": "https://www.rsstranslator.com",
        "X-Title": "RSS Translator",
    }

    class Meta:
        verbose_name = "OpenAI"
        verbose_name_plural = "OpenAI"

    def _init(self):
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=120.0,
        )

    def validate(self) -> bool:
        if self.api_key:
            try:
                client = self._init()
                res = client.with_options(max_retries=3).chat.completions.create(
                    extra_headers=self.EXTRA_HEADERS,
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You must only reply with exactly one character: 1",
                        },
                        {"role": "user", "content": "1"},
                    ],
                    # max_tokens=50,
                    max_completion_tokens=50,
                )
                # 有些第三方源在key或url错误的情况下，并不会抛出异常代码，而是返回html广告，因此添加该行。
                fr = res.choices[0].finish_reason
                # 提交后台任务检测模型限制
                results = task_manager.submit_task(
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
                self.save()

    def detect_model_limit(self, force=False) -> int:
        """通过二分搜索来高效检测模型实际限制"""
        if not force and self.max_tokens > 0:
            return self.max_tokens

        # test_range = [1024, 4096, 8192, 16384, 32768, 65536, 128000, 200000, 400000, 500000, 1000000]

        # 二分搜索找到确切限制
        def binary_search_limit(low, high):
            """使用二分搜索找到确切的token限制"""
            if high - low <= 256:  # 当范围足够小时，返回低值作为安全限制
                return low

            mid = (low + high) // 2

            try:
                # 使用最小的测试内容减少token消耗
                response = self._init().chat.completions.create(
                    extra_headers=self.EXTRA_HEADERS,
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
        final_limit = binary_search_limit(1024, 1000000)
        self.max_tokens = final_limit
        self.save()
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

    def completions(
        self,
        text: str,
        system_prompt: str = None,
        user_prompt: str = None,
        _is_chunk: bool = False,  # 内部参数，用于标记是否为分块调用
        **kwargs,
    ) -> dict:
        client = self._init()
        tokens = 0
        result_text = ""

        try:
            if user_prompt:
                system_prompt += f"\n\n{user_prompt}"

            # 应用速率限制
            self._wait_for_rate_limit()

            # 计算系统提示的token占用
            system_prompt_tokens = get_token_count(system_prompt)
            # 获取最大可用token数（保留buffer）
            if not self.max_tokens:
                task_manager.submit_task(
                    f"detect_model_limit_{self.model}_{self.id}",
                    self.detect_model_limit,
                    force=True,
                )
                raise ValueError(
                    "max_tokens is not set, Please wait for the model limit detection to complete"
                )

            max_usable_tokens = (
                self.max_tokens - system_prompt_tokens - 100
            )  # 100 token buffer
            # 检查文本长度是否需要分块
            if get_token_count(text) > max_usable_tokens:
                logger.info(
                    f"Text too large ({get_token_count(text)} tokens), chunking..."
                )

                # 使用自适应分块
                chunks = adaptive_chunking(
                    text,
                    target_chunks=max(1, int(len(text) / max_usable_tokens)),
                    min_chunk_size=500,
                    max_chunk_size=max_usable_tokens,
                )

                # 分块翻译
                translated_chunks = []
                for chunk in chunks:
                    result = self.completions(
                        text=chunk,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        _is_chunk=True,  # 标记为分块调用
                        **kwargs,
                    )
                    translated_chunks.append(result["text"])
                    tokens += result["tokens"]

                result_text = " ".join(translated_chunks)
                return {"text": result_text, "tokens": tokens}

            # 计算合理的输出token限制
            input_tokens = get_token_count(system_prompt) + get_token_count(text)
            # 输出token限制 = 模型总限制 - 输入token - 安全缓冲
            output_token_limit = min(
                4096,  # 大多数场景下4096个输出token足够
                max(
                    512, self.max_tokens - input_tokens - 200
                ),  # 至少512，最多为剩余空间-200缓冲
            )

            # 正常流程
            res = client.with_options(max_retries=3).chat.completions.create(
                extra_headers=self.EXTRA_HEADERS,
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=self.temperature,
                top_p=self.top_p,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                # max_tokens=output_token_limit,
                max_completion_tokens=output_token_limit,
                reasoning_effort="minimal",  # 关闭深度思考
            )
            if (
                res.choices
                and res.choices[0].finish_reason == "stop"
                and res.choices[0].message.content
            ):
                result_text = res.choices[0].message.content
                logger.debug(f"[{self.name}]: {result_text[:50]}...")

            tokens = res.usage.total_tokens if res.usage else 0
        except Exception as e:
            self.log = f"{timezone.now()}: {str(e)}"
            logger.error(f"{self.name}: {e}")

        if not _is_chunk:
            self.save()

        return {"text": result_text, "tokens": tokens}

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
        target_language: str,
        system_prompt: str,
        **kwargs,
    ) -> dict:
        logger.info(f">>> Start Digesting [{target_language}]: {text[:50]}...")
        system_prompt += settings.output_format_for_filter_prompt
        return self.completions(text, system_prompt=system_prompt, **kwargs)

    def filter(self, text: str, system_prompt: str, **kwargs) -> dict:
        logger.info(f">>> Start Filter: {text[:50]}...")
        passed = False
        tokens = 0
        results = self.completions(
            text,
            system_prompt=system_prompt + settings.output_format_for_filter_prompt,
            **kwargs,
        )

        if results["text"] and "Passed" in results["text"]:
            logger.info(">>> Filter Passed")
            passed = True
            tokens = results["tokens"]
        else:
            logger.info(">>> Filter Blocked")
            passed = False

        return {"passed": passed, "tokens": tokens}


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

            with request.urlopen(req, timeout=5) as response:
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

    def summarize(self, text: str, target_language: str) -> dict:
        logger.info(">>> Test Summarize [%s]: %s", target_language, text)
        time.sleep(self.interval)
        return {"text": self.translated_text, "tokens": 10, "characters": len(text)}

    def filter(self, text: str, **kwargs):
        logger.info(">>> Test Filter")
        import random

        time.sleep(self.interval)
        return {"passed": random.choice([True, False]), "tokens": 10}
