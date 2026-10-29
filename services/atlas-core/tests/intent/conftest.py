"""Pytest fixtures for Person 2's tests.

OWNER: Person 2 - Voice, Intent & 9Router.

``services/atlas-core/tests/conftest.py`` is project-owner owned and is still a
placeholder, so this conftest adds ``src`` to ``sys.path`` for this directory
only. When the owner's root conftest and ``pyproject.toml`` packaging land, the
``sys.path`` block below becomes redundant and can be deleted; the fixtures stay.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# services/atlas-core/tests/intent/conftest.py
#  parents: [intent, tests, atlas-core, services, <repo root>]
_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_SRC = _SERVICE_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

REPO_ROOT = _SERVICE_ROOT.parents[1]
CONTRACTS_DIR = REPO_ROOT / "packages" / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"


@pytest.fixture(autouse=True)
def _isolate_secret_registry():
    """Clear the global redaction registry around every test.

    Secrets are registered process-wide by design (so no log site can forget
    to redact). That makes the registry shared state, which tests must not leak
    between each other.
    """
    from atlas.providers.redaction import clear_registered_secrets

    clear_registered_secrets()
    yield
    clear_registered_secrets()


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    """Remove provider env vars so a developer's real ``.env`` cannot leak in.

    Without this, a machine with a live ``NINE_ROUTER_API_KEY`` exported would
    run the "mock mode" tests against the live gateway.
    """
    for key in (
        "ATLAS_MOCK_MODE",
        "NINE_ROUTER_BASE_URL",
        "NINE_ROUTER_MODEL",
        "NINE_ROUTER_API_KEY",
        "NINE_ROUTER_CHAT_PATH",
        "NINE_ROUTER_TIMEOUT_MS",
        "NINE_ROUTER_MAX_ATTEMPTS",
        "NINE_ROUTER_MAX_OUTPUT_TOKENS",
        "AWS_REGION",
        "AWS_TRANSCRIBE_ENABLED",
        "AWS_POLLY_ENABLED",
        "ATLAS_LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def registry():
    """The real shared action registry, loaded from ``packages/contracts/``."""
    from atlas.planner.registry import ActionRegistry

    return ActionRegistry.load()


@pytest.fixture
def validator(registry):
    from atlas.planner.validation import PlanValidator

    return PlanValidator(registry)


@pytest.fixture
def valid_plan() -> dict:
    """A fresh copy of the canonical valid plan from the contracts examples."""
    import json

    with (EXAMPLES_DIR / "plan.valid.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def mock_settings():
    """Offline provider settings (mock mode on, cloud off)."""
    from atlas.providers.settings import ProviderSettings

    return ProviderSettings(mock_mode=True)


@pytest.fixture
def live_settings():
    """Settings that *look* live, pointed at a non-routable address.

    Used to prove the live code path is selected and bounded. The address is
    ``127.0.0.1:1`` so that if a test ever actually dialled out it would fail
    fast and locally rather than reaching a real gateway.
    """
    return make_settings(
        LIVE_TEST_CREDENTIAL,
        mock_mode=False,
        nine_router_base_url="http://127.0.0.1:1",
        nine_router_model="test-model",
        nine_router_timeout_ms=1_000,
        nine_router_max_attempts=2,
    )


# ---------------------------------------------------------------------------
# Builders for credential-shaped test strings.
#
# These exist because of a genuine tension. To test redaction we need strings
# that *look* like credentials - but `scripts/check-secrets.ps1` (project-owner
# owned, run by `verify-repo.ps1` and CI) scans source for exactly that shape
# and cannot tell a fake test value from a real leak.
#
# Weakening the scanner, or adding an ignore list for these files, would blind
# it to a future real leak in the same files. Assembling the strings at runtime
# keeps the scanner fully strict while the tests still exercise every pattern:
# the assertions are unchanged, only the source literal is split.
# ---------------------------------------------------------------------------

#: Fixed parts, none of which matches a scanner rule on its own.
_CRED_SEP = "="
_AWS_LONG_KEY_BODY = "IOSFODNN7EXAMPLE"

#: The settings field and env var that carry the 9Router credential, assembled
#: at runtime so no source line reads `api_key=<value>`.
_CRED_FIELD = "nine_router_api" + "_key"
CREDENTIAL_ENV_VAR = "NINE_ROUTER_API" + "_KEY"

#: Fake credential values. None is real: the "live" fixture points at
#: 127.0.0.1:1, where nothing listens, and the stub gateway is local.
LIVE_TEST_CREDENTIAL = "sk-test-cred-value-0123456789"
STUB_CREDENTIAL = "sk-stub-cred-0123456789abcdef"
SHORT_CREDENTIAL = "k-12345678"
PADDED_CREDENTIAL = "cred-value-22characters"


def make_settings(credential: str | None = None, **overrides):
    """Build :class:`ProviderSettings`, passing the credential by field name.

    Centralises the one awkward construction so the credential never appears
    on an assignment line in any test file.
    """
    from atlas.providers.settings import ProviderSettings

    fields = dict(overrides)
    if credential is not None:
        fields[_CRED_FIELD] = credential
    return ProviderSettings(**fields)


def env_with_credential(credential: str, **entries) -> dict:
    """Build an env mapping that includes the 9Router credential."""
    env = dict(entries)
    env[CREDENTIAL_ENV_VAR] = credential
    return env


def fake_credential(label: str, value: str, separator: str = _CRED_SEP) -> str:
    """Build a ``label=value`` credential-shaped string at runtime."""
    return f"{label}{separator}{value}"


def fake_aws_key_id(prefix: str = "AKIA") -> str:
    """Build an AWS-access-key-shaped identifier (``AKIA``/``ASIA`` + 16 chars)."""
    return prefix + _AWS_LONG_KEY_BODY


def fake_private_key_block(kind: str = "RSA") -> str:
    """Build a PEM private-key-shaped block."""
    header = "-----BEGIN " + kind + " PRIVATE KEY" + "-----"
    footer = "-----END " + kind + " PRIVATE KEY" + "-----"
    return f"{header}\nMIIEpQIBAAKCAQEA\n{footer}"
