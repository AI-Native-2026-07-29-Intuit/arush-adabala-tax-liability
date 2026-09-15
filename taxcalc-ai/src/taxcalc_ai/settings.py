# taxcalc-ai/src/taxcalc_ai/settings.py
"""Pydantic Settings - 12-factor config for the taxcalc-api sidecar.

One class reads every knob this process has, from the environment, at boot. That is the point
of consolidating it here: a missing or malformed value fails the process on startup with a
``ValidationError`` naming the field, instead of failing the first request that happens to need
it - possibly hours later, in production, under load.

Three settings choices carry most of the weight:

``extra="forbid"``
    A ``TAXCALC_AI_PROXY_TIMEOUT`` typo'd from ``TAXCALC_AI_PROXY_TIMEOUT_SECONDS`` is an error,
    not a silently ignored variable and a silently defaulted timeout.

``frozen=True``
    Configuration read at boot stays what it was at boot. Code that wants a different timeout
    for one call passes one; it does not reach in and edit the process's config.

``proxy_api_key: SecretStr``
    Not ``str``. ``SecretStr`` renders as ``**********`` in ``repr()``, in ``str()``, and in
    ``model_dump()`` - which means the key survives a naive ``LOG.info("settings=%s", settings)``
    and a crash traceback that prints locals. ``.get_secret_value()`` is the single deliberate
    escape hatch, and :mod:`taxcalc_ai.client` calls it in exactly one place: the line that
    builds the ``authorization`` header.

``secrets_dir`` points at ``/run/secrets`` so a Kubernetes-mounted secret file named
``taxcalc_ai_proxy_api_key`` is picked up with no code change - the same value arriving as a
file in the cluster and as an env var on a laptop.
"""

from __future__ import annotations

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TaxcalcAiSettings(BaseSettings):
    """Every environment-driven knob for the sidecar, validated at process boot."""

    model_config = SettingsConfigDict(
        env_prefix="TAXCALC_AI_",
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir="/run/secrets",
        extra="forbid",
        frozen=True,
    )

    # The W3 D1 LLM proxy the sidecar calls. In this repo the proxy is served by the Java
    # service itself at POST /v1/completions, so this is normally the taxcalc-api base URL.
    proxy_base_url: HttpUrl
    # Secret - never logged. .get_secret_value() is the only escape hatch, used once, in client.py.
    proxy_api_key: SecretStr
    proxy_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    proxy_max_retries: int = Field(default=3, ge=0, le=10)
    model_id: str = Field(default="claude-haiku-4-5", min_length=1, max_length=128)
    tenant_id: str = Field(default="shared", min_length=1, max_length=64)
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARN|ERROR)$")
