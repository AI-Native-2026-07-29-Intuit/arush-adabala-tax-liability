# taxcalc-ai/tests/test_settings.py
"""Settings tests - the 12-factor read, and the secret discipline around the API key."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from taxcalc_ai.settings import TaxcalcAiSettings
from tests.conftest import PROXY_API_KEY, PROXY_BASE_URL


def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every field comes from a ``TAXCALC_AI_``-prefixed environment variable."""
    monkeypatch.setenv("TAXCALC_AI_PROXY_BASE_URL", PROXY_BASE_URL)
    monkeypatch.setenv("TAXCALC_AI_PROXY_API_KEY", PROXY_API_KEY)
    monkeypatch.setenv("TAXCALC_AI_TENANT_ID", "tenant-a")

    settings = TaxcalcAiSettings()

    assert settings.tenant_id == "tenant-a"
    assert settings.proxy_max_retries == 3
    assert settings.proxy_api_key.get_secret_value() == PROXY_API_KEY


def test_secret_never_appears_in_repr(settings: TaxcalcAiSettings) -> None:
    """``SecretStr`` is the reason a stray ``LOG.info("%s", settings)`` is survivable."""
    assert PROXY_API_KEY not in repr(settings)
    assert PROXY_API_KEY not in str(settings)
    assert PROXY_API_KEY not in str(settings.model_dump())
    assert "**********" in repr(settings)


def test_settings_is_frozen(settings: TaxcalcAiSettings) -> None:
    """Config read at boot stays what it was at boot."""
    with pytest.raises(ValidationError):
        settings.tenant_id = "tenant-b"  # type: ignore[misc]


def test_settings_rejects_missing_required_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing secret fails the process at boot, not on the first request that needs it."""
    monkeypatch.setenv("TAXCALC_AI_PROXY_BASE_URL", PROXY_BASE_URL)
    monkeypatch.delenv("TAXCALC_AI_PROXY_API_KEY", raising=False)
    # env_file="" defeats a developer's local .env, which would otherwise supply the key.
    with pytest.raises(ValidationError) as excinfo:
        TaxcalcAiSettings(_env_file=None)
    assert "proxy_api_key" in str(excinfo.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TAXCALC_AI_LOG_LEVEL", "CHATTY"),
        ("TAXCALC_AI_PROXY_TIMEOUT_SECONDS", "0.1"),
        ("TAXCALC_AI_PROXY_MAX_RETRIES", "99"),
        ("TAXCALC_AI_PROXY_BASE_URL", "not-a-url"),
    ],
)
def test_settings_rejects_nonsensical_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """Out-of-range or malformed config is a boot failure naming the offending field."""
    monkeypatch.setenv("TAXCALC_AI_PROXY_BASE_URL", PROXY_BASE_URL)
    monkeypatch.setenv("TAXCALC_AI_PROXY_API_KEY", PROXY_API_KEY)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        TaxcalcAiSettings()
