# taxcalc-ai/tests/conftest.py
"""Shared fixtures.

Every fixture builds its object from explicit values rather than from ambient state: the
settings fixture sets real environment variables through ``monkeypatch`` so the test exercises
the same env-reading path production does, and ``monkeypatch`` unwinds them afterwards so one
test cannot leak configuration into the next.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from taxcalc_ai.models import Liability, LiabilityEstimateRequest, Taxpayer
from taxcalc_ai.settings import TaxcalcAiSettings

FIXTURES = Path(__file__).parent / "fixtures"

#: The captured Java-side JSON for GET /api/v1/taxpayers/taxpayer-001 - the verbatim response
#: body of that request against a locally-running taxcalc-api. See PYTHON.md, "The round-trip
#: fixture", for the exact capture procedure.
JAVA_TAXPAYER_JSON = FIXTURES / "taxpayer_java.json"

PROXY_BASE_URL = "https://proxy.example.internal"
PROXY_API_KEY = "key_synth_abc123"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> TaxcalcAiSettings:
    """A fully-populated settings object, read from test-only environment variables."""
    monkeypatch.setenv("TAXCALC_AI_PROXY_BASE_URL", PROXY_BASE_URL)
    monkeypatch.setenv("TAXCALC_AI_PROXY_API_KEY", PROXY_API_KEY)
    monkeypatch.setenv("TAXCALC_AI_TENANT_ID", "tenant-a")
    monkeypatch.setenv("TAXCALC_AI_PROXY_MAX_RETRIES", "3")
    # Sub-second so the retry test does not actually sleep out the exponential backoff.
    monkeypatch.setenv("TAXCALC_AI_PROXY_TIMEOUT_SECONDS", "1.0")
    return TaxcalcAiSettings()


@pytest.fixture
def java_taxpayer_json() -> bytes:
    """The captured Java-side JSON bytes, read fresh for each test that wants them."""
    return JAVA_TAXPAYER_JSON.read_bytes()


@pytest.fixture
def taxpayer() -> Taxpayer:
    """A valid taxpayer with one liability, built with snake_case (populate_by_name)."""
    return Taxpayer(
        id="taxpayer-001",
        tenant_id="tenant-shared",
        display_name="Ada Lovelace",
        filing_status="SINGLE",
        home_jurisdiction="CALIFORNIA",
        created_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
        liabilities=(
            Liability(
                tax_year=2026,
                bracket_id="bracket-ca-2026-mid",
                taxable_amount=Decimal("120000.00"),
                liability_amount=Decimal("26400.00"),
                computed_at=datetime(2026, 1, 15, 12, 0, 5, tzinfo=UTC),
            ),
        ),
        tags=("w7d1", "synthetic"),
    )


@pytest.fixture
def estimate_request(taxpayer: Taxpayer) -> LiabilityEstimateRequest:
    """A valid request envelope wrapping the ``taxpayer`` fixture."""
    return LiabilityEstimateRequest(correlation_id="corr-w7d1-0001", taxpayer=taxpayer)
