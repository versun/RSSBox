"""Read shared runtime settings at operation boundaries, not process startup.

No process-local cache: cron subprocesses and existing web workers see committed
changes. Never create configuration rows on GET/read; a missing row uses env.
"""
from dataclasses import dataclass
from django.conf import settings


@dataclass(frozen=True)
class RuntimePolicy:
    request_timeout: float
    max_attempts: int
    retry_base_seconds: int
    retry_max_seconds: int
    attention_after_attempts: int


def get_runtime_policy() -> RuntimePolicy:
    from core.models import RuntimeConfiguration

    config = RuntimeConfiguration.objects.filter(pk=1).first()

    def effective(field, fallback):
        value = getattr(config, field, None)
        return value if value is not None else fallback

    base = effective("retry_base_seconds", settings.ENTRY_RETRY_BASE_DELAY_SECONDS)
    return RuntimePolicy(
        request_timeout=effective("request_timeout", settings.OPENAI_API_TIMEOUT),
        max_attempts=effective("max_attempts", settings.ENTRY_MAX_RETRY_ATTEMPTS),
        retry_base_seconds=base,
        retry_max_seconds=max(base, effective("retry_max_seconds", settings.ENTRY_RETRY_MAX_DELAY_SECONDS)),
        attention_after_attempts=effective("attention_after_attempts", 1),
    )
