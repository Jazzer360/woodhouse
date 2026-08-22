"""Structured redaction for diagnostic metadata at the Tesla boundary."""

from collections.abc import Mapping, Sequence

from tesla_personal_platform.tesla_client.models import JsonObject, JsonValue

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "calendar_data",
        "client_secret",
        "code",
        "id_token",
        "lat",
        "latitude",
        "lon",
        "longitude",
        "password",
        "pin",
        "refresh_token",
        "routable_message",
        "token",
        "vin",
    }
)


def redact_mapping(value: Mapping[str, object]) -> JsonObject:
    """Return log-safe structural metadata without secret/location-bearing values."""

    return {
        str(key): REDACTED if str(key).casefold() in _SENSITIVE_KEYS else _redact(item)
        for key, item in value.items()
    }


def _redact(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return redact_mapping({str(key): item for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_redact(item) for item in value]
    return f"<{type(value).__name__}>"
