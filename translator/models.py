import logging
from django.db import models
from django.utils.translation import gettext_lazy as _
from config import settings
from openai import OpenAI
from encrypted_model_fields.fields import EncryptedCharField
from time import sleep
from utils.text_handler import get_token_count, adaptive_chunking


class TranslatorEngine(models.Model):
    name = models.CharField(_("Name"), max_length=100, unique=True)
    valid = models.BooleanField(_("Valid"), null=True)
    is_ai = models.BooleanField(default=False, editable=False)

    def translate(
        self, text: str, target_language: str, source_language: str = "auto", **kwargs
    ) -> dict:
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


class OpenAITranslator(TranslatorEngine):
    # https://platform.openai.com/docs/api-reference/chat
    is_ai = models.BooleanField(default=True, editable=False)
    api_key = EncryptedCharField(_("API Key"), max_length=255)
    base_url = models.URLField(_("API URL"), default="https://api.openai.com/v1")
    model = models.CharField(
        max_length=100,
        default="gpt-3.5-turbo",
        help_text="e.g. gpt-3.5-turbo, gpt-4-turbo",
    )
    translate_prompt = models.TextField(
        _("Title Translate Prompt"), default=settings.default_title_translate_prompt
    )
    content_translate_prompt = models.TextField(
        _("Content Translate Prompt"), default=settings.default_content_translate_prompt
    )

    temperature = models.FloatField(default=0.2)
    top_p = models.FloatField(default=0.2)
    frequency_penalty = models.FloatField(default=0)
    presence_penalty = models.FloatField(default=0)
    max_tokens = models.IntegerField(default=1000000)

    summary_prompt = models.TextField(default=settings.default_summary_prompt)

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
                    model=self.model,
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=10,
                )
                fr = res.choices[
                    0
                ].finish_reason  # 有些第三方源在key或url错误的情况下，并不会抛出异常代码，而是返回html广告，因此添加该行。
                logging.info(">>> Translator Validate:%s", fr)
                return True
            except Exception as e:
                logging.error("OpenAIInterface validate ->%s", e)
                return False

    def translate(
        self,
        text: str,
        target_language: str,
        system_prompt: str = None,
        user_prompt: str = None,
        text_type: str = "title",
        max_tokens: int = None,
        **kwargs,
    ) -> dict:
        logging.info(">>> Translate [%s]: %s", target_language, text)
        client = self._init()
        tokens = 0
        translated_text = ""
        system_prompt = (
            system_prompt or self.translate_prompt
            if text_type == "title"
            else self.content_translate_prompt
        )
        try:
            system_prompt = system_prompt.replace("{target_language}", target_language)
            if user_prompt:
                system_prompt += f"\n\n{user_prompt}"

            # 计算系统提示的token占用
            system_prompt_tokens = get_token_count(system_prompt)
            # 计算最大可用token数（保留buffer）
            if max_tokens is None:
                max_tokens = self.max_tokens
            max_usable_tokens = max_tokens - system_prompt_tokens - 100  # 100 token buffer
            # 检查文本长度是否需要分块
            if get_token_count(text) > max_usable_tokens:
                logging.info(f"Text too large ({get_token_count(text)} tokens), chunking...")
                
                # 使用自适应分块
                chunks = adaptive_chunking(
                    text,
                    target_chunks=max(1, int(len(text) / max_usable_tokens)),
                    min_chunk_size=500,
                    max_chunk_size=max_usable_tokens
                )
                
                # 分块翻译
                translated_chunks = []
                for chunk in chunks:
                    result = self.translate(
                        text=chunk,
                        target_language=target_language,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        text_type=text_type,
                        **kwargs
                    )
                    translated_chunks.append(result["text"])
                    tokens += result["tokens"]
                
                translated_text = " ".join(translated_chunks)
                return {"text": translated_text, "tokens": tokens}
            
            # 正常翻译流程
            res = client.with_options(max_retries=3).chat.completions.create(
                extra_headers={
                    "HTTP-Referer": "https://www.rsstranslator.com",
                    "X-Title": "RSS Translator",
                },
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=self.temperature,
                top_p=self.top_p,
                frequency_penalty=self.frequency_penalty,
                presence_penalty=self.presence_penalty,
                max_tokens=self.max_tokens,
            )
            # if res.choices[0].finish_reason.lower() == "stop" or res.choices[0].message.content:
            if res.choices and res.choices[0].message.content:
                translated_text = res.choices[0].message.content
                logging.info(
                    "OpenAITranslator->%s: %s",
                    res.choices[0].finish_reason,
                    translated_text,
                )
            # else:
            #     translated_text = ''
            #     logging.warning("Translator->%s: %s", res.choices[0].finish_reason, text)
            tokens = res.usage.total_tokens if res.usage else 0
        except Exception as e:
            logging.error("OpenAIInterface->%s: %s", e, text)

        return {"text": translated_text, "tokens": tokens}

    def summarize(self, text: str, target_language: str, max_tokens: int = None, **kwargs) -> dict:
        logging.info(">>> Summarize [%s]: %s", target_language, text)
        return self.translate(text, target_language, system_prompt=self.summary_prompt, max_tokens=max_tokens, **kwargs)


class DeepLTranslator(TranslatorEngine):
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
        try:
            translator = self._init()
            usage = translator.get_usage()
            return usage.character.valid
        except Exception as e:
            logging.error("DeepLTranslator validate ->%s", e)
            return False

    def translate(self, text: str, target_language: str, **kwargs) -> dict:
        logging.info(">>> DeepL Translate [%s]: %s", target_language, text)
        target_code = self.language_code_map.get(target_language, None)
        translated_text = ""
        try:
            if target_code is None:
                logging.error(
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
            logging.error("DeepLTranslator->%s: %s", e, text)
        return {"text": translated_text, "characters": len(text)}


class TestTranslator(TranslatorEngine):
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
        logging.info(">>> Test Translate [%s]: %s", target_language, text)
        sleep(self.interval)
        return {"text": self.translated_text, "tokens": 0, "characters": len(text)}

    def summarize(self, text: str, target_language: str) -> dict:
        logging.info(">>> Test Summarize [%s]: %s", target_language, text)
        return self.translate(text, target_language)
