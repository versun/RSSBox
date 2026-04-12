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
from core.tasks.task_manager import task_manager
from core.services.agent.openai import (
    openai_advanced_default,
    openai_completions,
    openai_detect_model_limit,
    openai_filter,
    openai_init,
    openai_summarize,
    openai_translate,
    openai_validate,
    openai_wait_for_rate_limit,
)
from core.services.agent.deepl import (
    deepl_init,
    deepl_translate,
    deepl_validate,
)
from core.services.agent.libretranslate import (
    libretranslate_api_languages,
    libretranslate_api_request,
    libretranslate_api_translate,
    libretranslate_translate,
    libretranslate_validate,
)
from core.services.agent.test_agent import (
    testagent_filter,
    testagent_summarize,
    testagent_translate,
)

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

    advanced_params = models.JSONField(
        default=openai_advanced_default,
        help_text=("Advanced OpenAI chat params as JSON."),
    )
    max_tokens = models.IntegerField(
        default=0, help_text="0 means detect automatically"
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

    def _init(self):
        return openai_init(
            self,
            openai_client_cls=OpenAI,
            settings_module=settings,
        )

    def validate(self) -> bool:
        return openai_validate(
            self,
            init_client=self._init,
            wait_for_rate_limit=self._wait_for_rate_limit,
            task_submit=task_manager.submit_task,
            logger=logger,
            settings_module=settings,
            timezone_module=timezone,
            save_func=self.save,
        )

    def detect_model_limit(self, force=False) -> int:
        return openai_detect_model_limit(
            self,
            force=force,
            init_client=self._init,
            wait_for_rate_limit=self._wait_for_rate_limit,
            logger=logger,
        )

    def _wait_for_rate_limit(self):
        return openai_wait_for_rate_limit(
            self,
            cache_backend=cache,
            datetime_module=datetime,
            sleep_func=time.sleep,
            logger=logger,
        )

    def completions(
        self,
        text: str,
        system_prompt: str = None,
        user_prompt: str = None,
        _is_chunk: bool = False,  # 内部参数，用于标记是否为分块调用
        **kwargs,
    ) -> dict:
        return openai_completions(
            self,
            text,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            _is_chunk=_is_chunk,
            init_client=self._init,
            wait_for_rate_limit=self._wait_for_rate_limit,
            task_submit=task_manager.submit_task,
            logger=logger,
            settings_module=settings,
            get_token_count_func=get_token_count,
            adaptive_chunking_func=adaptive_chunking,
            save_func=self.save,
            **kwargs,
        )

    def translate(
        self,
        text: str,
        target_language: str,
        user_prompt: str = None,
        text_type: str = "title",
        **kwargs,
    ) -> dict:
        return openai_translate(
            self,
            text,
            target_language,
            user_prompt=user_prompt,
            text_type=text_type,
            completions_func=self.completions,
            logger=logger,
            **kwargs,
        )

    def summarize(self, text: str, target_language: str, **kwargs) -> dict:
        return openai_summarize(
            self,
            text,
            target_language,
            completions_func=self.completions,
            logger=logger,
            **kwargs,
        )

    def filter(self, text: str, system_prompt: str, **kwargs) -> dict:
        return openai_filter(
            self,
            text,
            system_prompt,
            completions_func=self.completions,
            logger=logger,
            settings_module=settings,
            **kwargs,
        )


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
        return deepl_init(
            self,
            translator_cls=deepl.Translator,
        )

    def validate(self) -> bool:
        return deepl_validate(
            self,
            init_client=self._init,
            logger=logger,
            timezone_module=timezone,
            save_func=self.save,
        )

    def translate(self, text: str, target_language: str, **kwargs) -> dict:
        return deepl_translate(
            self,
            text,
            target_language,
            init_client=self._init,
            logger=logger,
            timezone_module=timezone,
            save_func=self.save,
        )


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
        return libretranslate_api_request(
            self,
            endpoint,
            params=params,
            method=method,
            request_module=request,
            parse_module=parse,
            json_module=json,
            settings_module=settings,
        )

    def _api_translate(
        self, q: str, source: str, target: str, format: str = "html"
    ) -> str:
        return libretranslate_api_translate(
            self,
            q,
            source,
            target,
            format=format,
            api_request_func=self._api_request,
        )

    def _api_languages(self) -> list:
        return libretranslate_api_languages(
            self,
            api_request_func=self._api_request,
        )

    # --------------------------------
    # Agent Methods
    # --------------------------------
    def validate(self) -> bool:
        return libretranslate_validate(
            self,
            api_languages_func=self._api_languages,
            timezone_module=timezone,
            save_func=self.save,
        )

    def translate(self, text: str, target_language: str, **kwargs) -> dict:
        return libretranslate_translate(
            self,
            text,
            target_language,
            api_translate_func=self._api_translate,
            logger=logger,
            timezone_module=timezone,
            save_func=self.save,
        )

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
        return testagent_translate(
            self,
            text,
            target_language,
            logger=logger,
            sleep_func=time.sleep,
        )

    def summarize(self, text: str, target_language: str, **kwargs) -> dict:
        return testagent_summarize(
            self,
            text,
            target_language,
            logger=logger,
            sleep_func=time.sleep,
        )

    def filter(self, text: str, **kwargs):
        import random

        return testagent_filter(
            self,
            logger=logger,
            sleep_func=time.sleep,
            random_choice=random.choice,
        )
