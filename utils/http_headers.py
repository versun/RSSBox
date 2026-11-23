import json
import re
from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# RFC 9110 Section 5.6.2 token characters for HTTP header field names.
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# Lowercased header names that cannot be overridden via custom headers.
# - authorization: conflicts with OpenAIAgent.api_key authentication
# - host/content-length/transfer-encoding/connection/content-type: framing headers
FORBIDDEN_HEADERS = frozenset(
    {
        "authorization",
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "content-type",
    }
)

MAX_CUSTOM_HEADERS = 32
MAX_HEADER_NAME_LENGTH = 256
MAX_HEADER_VALUE_LENGTH = 4096


class _JsonObjectPairs(list):
    """Sentinel list returned by json.loads object_pairs_hook for JSON objects."""


def parse_and_validate_headers(raw: str | Mapping[str, Any] | None) -> dict[str, str]:
    """Parse and validate custom HTTP headers from a JSON string or mapping."""
    if raw is None:
        return {}

    items: list[tuple[Any, Any]] | None = None
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped, object_pairs_hook=_JsonObjectPairs)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                _("Custom HTTP headers must be a valid JSON object.")
            ) from exc
        if isinstance(parsed, _JsonObjectPairs):
            items = parsed
    elif isinstance(raw, Mapping):
        items = list(raw.items())

    if items is None:
        raise ValidationError(
            _(
                "Custom HTTP headers must be a JSON object mapping header names "
                "to string values."
            )
        )

    if len(items) > MAX_CUSTOM_HEADERS:
        raise ValidationError(
            _("Too many custom HTTP headers (maximum %(max)d)."),
            params={"max": MAX_CUSTOM_HEADERS},
        )

    validated: dict[str, str] = {}
    seen_lower: set[str] = set()

    for raw_name, raw_value in items:
        if not isinstance(raw_name, str):
            raise ValidationError(_("HTTP header names must be strings."))
        if not isinstance(raw_value, str):
            raise ValidationError(
                _("Value for HTTP header '%(name)s' must be a string."),
                params={"name": raw_name},
            )

        if not raw_name:
            raise ValidationError(_("HTTP header name cannot be empty."))

        if len(raw_name) > MAX_HEADER_NAME_LENGTH:
            raise ValidationError(
                _("HTTP header name '%(name)s' exceeds maximum length of %(max)d."),
                params={"name": raw_name[:32], "max": MAX_HEADER_NAME_LENGTH},
            )

        if not HEADER_NAME_RE.fullmatch(raw_name):
            raise ValidationError(
                _(
                    "Invalid HTTP header name '%(name)s'. "
                    "Header names must use RFC 9110 token characters only."
                ),
                params={"name": raw_name},
            )

        lower_name = raw_name.lower()
        if lower_name in seen_lower:
            raise ValidationError(
                _("Duplicate HTTP header name (case-insensitive): '%(name)s'."),
                params={"name": raw_name},
            )

        if lower_name in FORBIDDEN_HEADERS:
            if lower_name == "authorization":
                raise ValidationError(
                    _(
                        "Header 'Authorization' is not allowed. "
                        "Use the API Key field for authentication instead."
                    )
                )
            raise ValidationError(
                _("Header '%(name)s' is reserved and cannot be customized."),
                params={"name": raw_name},
            )

        if len(raw_value) > MAX_HEADER_VALUE_LENGTH:
            raise ValidationError(
                _(
                    "Value for HTTP header '%(name)s' exceeds maximum length "
                    "of %(max)d characters."
                ),
                params={"name": raw_name, "max": MAX_HEADER_VALUE_LENGTH},
            )

        if any((ord(ch) < 0x20 and ch != "\t") or ord(ch) == 0x7F for ch in raw_value):
            raise ValidationError(
                _(
                    "Invalid value for HTTP header '%(name)s': "
                    "control characters and line breaks are not allowed."
                ),
                params={"name": raw_name},
            )

        if raw_value != raw_value.strip(" \t"):
            raise ValidationError(
                _(
                    "Invalid value for HTTP header '%(name)s': "
                    "leading or trailing spaces/tabs are not allowed."
                ),
                params={"name": raw_name},
            )

        try:
            raw_value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValidationError(
                _(
                    "Invalid value for HTTP header '%(name)s': "
                    "header values must only contain ASCII characters."
                ),
                params={"name": raw_name},
            ) from exc

        seen_lower.add(lower_name)
        validated[raw_name] = raw_value

    return validated


def serialize_headers(headers: Mapping[str, str] | None) -> str:
    """Serialize validated headers to canonical JSON string (or empty string)."""
    if not headers:
        return ""
    return json.dumps(dict(headers), ensure_ascii=True)


def merge_headers(
    defaults: Mapping[str, str], overrides: Mapping[str, str]
) -> dict[str, str]:
    """Merge header mappings case-insensitively, letting overrides win."""
    merged: dict[str, str] = {}
    key_by_lower: dict[str, str] = {}

    for source in (defaults, overrides):
        for name, value in source.items():
            lower = name.lower()
            if lower in key_by_lower:
                del merged[key_by_lower[lower]]
            key_by_lower[lower] = name
            merged[name] = value

    return merged
