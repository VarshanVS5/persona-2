"""Provider settings and the action-registry view.

OWNER: Person 2 - Voice, Intent & 9Router.

Two themes:

* **Fail-safe configuration.** A missing, blank, or malformed value must land
  on the safe default - mock mode on, cloud off - never on a live call.
* **The registry is the single source of truth.** Action types, tiers,
  timeouts, and the blocked list come from
  ``packages/contracts/action-registry.json``, and a structurally broken
  registry must stop planning rather than be worked around.
"""

from __future__ import annotations

import json

import pytest
from conftest import PADDED_CREDENTIAL, SHORT_CREDENTIAL, env_with_credential

from atlas.planner.contracts import (
    ContractsUnavailable,
    clear_contract_cache,
    contract_version,
    contracts_dir,
)
from atlas.planner.registry import (
    RISK_TIERS,
    ActionRegistry,
    highest_risk_tier,
    risk_at_least,
)
from atlas.providers.settings import (
    DEFAULT_CHAT_PATH,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT_MS,
    ProviderSettings,
)

#: Credential-carrying env mappings are built with ``env_with_credential`` so
#: that no source line in this file reads ``API_KEY: <value>``. Keeping
#: ``scripts/check-secrets.ps1`` strict is worth the small indirection.
_LIVE_BASE = {"ATLAS_MOCK_MODE": "false", "NINE_ROUTER_BASE_URL": "https://gw.example"}


# -- fail-safe defaults ---------------------------------------------------


def test_empty_environment_is_offline_and_cloud_free():
    settings = ProviderSettings.from_mapping({})

    assert settings.mock_mode is True
    assert settings.aws_transcribe_enabled is False
    assert settings.aws_polly_enabled is False
    assert settings.use_live_llm is False


def test_env_example_values_select_mock_mode():
    """The checked-in ``.env.example`` must not produce a live configuration."""
    settings = ProviderSettings.from_mapping(
        {
            "ATLAS_MOCK_MODE": "true",
            "AWS_REGION": "ap-south-1",
            "AWS_TRANSCRIBE_ENABLED": "false",
            "AWS_POLLY_ENABLED": "false",
            "NINE_ROUTER_BASE_URL": "",
            "NINE_ROUTER_MODEL": "",
            "NINE_ROUTER_API_KEY": "",
        }
    )

    assert settings.mock_mode is True
    assert settings.nine_router_configured is False


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on", " True "])
def test_truthy_flags_parse(raw):
    assert ProviderSettings.from_mapping({"AWS_POLLY_ENABLED": raw}).aws_polly_enabled is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "", "  "])
def test_falsey_flags_parse(raw):
    assert ProviderSettings.from_mapping({"ATLAS_MOCK_MODE": raw}).mock_mode is False


def test_malformed_flag_keeps_the_safe_default():
    """A typo must not silently enable a cloud provider."""
    assert ProviderSettings.from_mapping({"AWS_POLLY_ENABLED": "ye"}).aws_polly_enabled is False
    assert ProviderSettings.from_mapping({"ATLAS_MOCK_MODE": "maybe"}).mock_mode is True


def test_malformed_integer_keeps_the_default():
    settings = ProviderSettings.from_mapping({"NINE_ROUTER_TIMEOUT_MS": "soon"})
    assert settings.nine_router_timeout_ms == DEFAULT_TIMEOUT_MS


@pytest.mark.parametrize(
    "raw,expected",
    [("500", 1_000), ("999999", 120_000), ("15000", 15_000)],
)
def test_timeout_is_clamped_to_a_sane_band(raw, expected):
    assert ProviderSettings.from_mapping({"NINE_ROUTER_TIMEOUT_MS": raw}).nine_router_timeout_ms == expected


@pytest.mark.parametrize("raw,expected", [("0", 1), ("99", 5), ("2", 2)])
def test_attempt_count_is_clamped(raw, expected):
    assert ProviderSettings.from_mapping({"NINE_ROUTER_MAX_ATTEMPTS": raw}).nine_router_max_attempts == expected


def test_default_attempt_count():
    assert ProviderSettings.from_mapping({}).nine_router_max_attempts == DEFAULT_MAX_ATTEMPTS


# -- URL composition ------------------------------------------------------


def test_default_chat_path_is_the_openai_compatible_one():
    settings = ProviderSettings.from_mapping({"NINE_ROUTER_BASE_URL": "https://gw.example"})

    assert settings.nine_router_chat_path == DEFAULT_CHAT_PATH
    assert settings.nine_router_url == "https://gw.example/v1/chat/completions"


@pytest.mark.parametrize(
    "base,path",
    [
        ("https://gw.example/", "/v1/chat/completions"),
        ("https://gw.example", "v1/chat/completions"),
        ("https://gw.example///", "///v1/chat/completions"),
    ],
)
def test_url_joining_tolerates_slashes(base, path):
    settings = ProviderSettings.from_mapping(
        {"NINE_ROUTER_BASE_URL": base, "NINE_ROUTER_CHAT_PATH": path}
    )
    assert settings.nine_router_url == "https://gw.example/v1/chat/completions"


def test_blank_chat_path_falls_back_to_the_default():
    settings = ProviderSettings.from_mapping(
        {"NINE_ROUTER_BASE_URL": "https://gw.example", "NINE_ROUTER_CHAT_PATH": "  "}
    )
    assert settings.nine_router_url.endswith(DEFAULT_CHAT_PATH)


def test_url_is_empty_without_a_base():
    assert ProviderSettings.from_mapping({}).nine_router_url == ""


def test_values_are_whitespace_trimmed():
    settings = ProviderSettings.from_mapping(
        env_with_credential(
            "  " + PADDED_CREDENTIAL + "  ",
            NINE_ROUTER_BASE_URL="  https://gw.example  ",
            NINE_ROUTER_MODEL=" m ",
        )
    )

    assert settings.nine_router_model == "m"
    assert settings.nine_router_api_key == PADDED_CREDENTIAL
    assert settings.nine_router_configured is True
    assert settings.nine_router_url == "https://gw.example/v1/chat/completions"


# -- live-mode gate -------------------------------------------------------


@pytest.mark.parametrize(
    "env",
    [
        # Mock mode off, nothing else configured.
        {"ATLAS_MOCK_MODE": "false"},
        # Base URL only: no model, no credential.
        dict(_LIVE_BASE),
        # Model and credential, but no base URL.
        env_with_credential(SHORT_CREDENTIAL, ATLAS_MOCK_MODE="false", NINE_ROUTER_MODEL="m"),
        # Base URL and credential, but no model.
        env_with_credential(SHORT_CREDENTIAL, **_LIVE_BASE),
    ],
    ids=["nothing", "url-only", "no-url", "no-model"],
)
def test_partial_configuration_never_goes_live(env):
    assert ProviderSettings.from_mapping(env).use_live_llm is False


def test_mock_mode_overrides_a_complete_configuration():
    """An operator who sets mock mode gets mock mode, credential or not."""
    settings = ProviderSettings.from_mapping(
        env_with_credential(
            SHORT_CREDENTIAL,
            ATLAS_MOCK_MODE="true",
            NINE_ROUTER_BASE_URL="https://gw.example",
            NINE_ROUTER_MODEL="m",
        )
    )

    assert settings.nine_router_configured is True
    assert settings.use_live_llm is False


def test_complete_configuration_with_mock_mode_off_goes_live():
    settings = ProviderSettings.from_mapping(
        env_with_credential(SHORT_CREDENTIAL, NINE_ROUTER_MODEL="m", **_LIVE_BASE)
    )
    assert settings.use_live_llm is True


def test_from_env_reads_the_process_environment(monkeypatch):
    for name, value in env_with_credential(
        SHORT_CREDENTIAL, NINE_ROUTER_MODEL="m", **_LIVE_BASE
    ).items():
        monkeypatch.setenv(name, value)

    assert ProviderSettings.from_env().use_live_llm is True


# -- registry -------------------------------------------------------------


def test_registry_loads_the_shared_contract(registry):
    assert registry.contract_version == "1.0.0"
    assert "app.open" in registry.action_types


def test_registered_action_exposes_its_full_definition(registry):
    action = registry.get("app.open")

    assert action.risk_tier == "T1"
    assert action.timeout_ms == 10_000
    assert action.required_arguments == ("appId",)
    assert action.allowed_arguments == ("appId",)
    assert action.precondition_types == {"app.allowlisted"}
    assert action.postcondition_types == {"window.exists"}
    assert action.on_failure == "stop_and_report"


def test_unknown_action_lookup_returns_none(registry):
    assert registry.get("shell.exec") is None
    assert registry.is_registered("shell.exec") is False


def test_all_blocked_categories_from_the_prd_are_present(registry):
    categories = {entry.category for entry in registry.blocked_categories}

    assert categories == {
        "credential.entry",
        "shell.exec",
        "security.uac_bypass",
        "file.delete_unrestricted",
        "message.send",
        "payment.purchase",
        "security.settings_change",
    }


def test_every_blocked_category_has_an_explainable_reason(registry):
    """The UI has to tell the user *why* something was refused."""
    for entry in registry.blocked_categories:
        assert entry.reason.strip()


def test_contract_version_is_reported():
    assert contract_version() == "1.0.0"


def test_contracts_dir_points_at_the_shared_package():
    assert contracts_dir().name == "contracts"
    assert (contracts_dir() / "action-registry.json").exists()


def test_contracts_dir_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_CONTRACTS_DIR", str(tmp_path))
    clear_contract_cache()
    try:
        assert contracts_dir() == tmp_path.resolve()
    finally:
        monkeypatch.delenv("ATLAS_CONTRACTS_DIR")
        clear_contract_cache()


# -- a broken registry stops planning ------------------------------------


def _write_registry(tmp_path, payload) -> None:
    (tmp_path / "action-registry.json").write_text(json.dumps(payload), encoding="utf-8")
    for name in ("task-plan.schema.json", "events.schema.json"):
        source = contracts_dir() / name
        (tmp_path / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"actions": []}, "empty allowlist"),
        ({"actions": [{"description": "no type"}]}, "missing actionType"),
        ({"actions": [{"actionType": "a.b", "riskTier": "T9", "timeoutMs": 1}]}, "bad tier"),
        ({"actions": [{"actionType": "a.b", "riskTier": "T1", "timeoutMs": 0}]}, "bad timeout"),
        ({"actions": [{"actionType": "a.b", "riskTier": "T1"}]}, "missing timeout"),
        (
            {
                "actions": [
                    {"actionType": "a.b", "riskTier": "T1", "timeoutMs": 1},
                    {"actionType": "a.b", "riskTier": "T1", "timeoutMs": 1},
                ]
            },
            "duplicate",
        ),
    ],
)
def test_structurally_broken_registry_is_fatal(monkeypatch, tmp_path, payload, reason):
    """Guessing an allowlist is the exact failure the policy model prevents."""
    _write_registry(tmp_path, payload)
    monkeypatch.setenv("ATLAS_CONTRACTS_DIR", str(tmp_path))
    clear_contract_cache()
    try:
        with pytest.raises(ContractsUnavailable):
            ActionRegistry.load()
    finally:
        monkeypatch.delenv("ATLAS_CONTRACTS_DIR")
        clear_contract_cache()


def test_missing_contract_file_is_fatal(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_CONTRACTS_DIR", str(tmp_path / "nope"))
    clear_contract_cache()
    try:
        with pytest.raises(ContractsUnavailable):
            ActionRegistry.load()
    finally:
        monkeypatch.delenv("ATLAS_CONTRACTS_DIR")
        clear_contract_cache()


def test_unparseable_contract_file_is_fatal(monkeypatch, tmp_path):
    (tmp_path / "action-registry.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("ATLAS_CONTRACTS_DIR", str(tmp_path))
    clear_contract_cache()
    try:
        with pytest.raises(ContractsUnavailable):
            ActionRegistry.load()
    finally:
        monkeypatch.delenv("ATLAS_CONTRACTS_DIR")
        clear_contract_cache()


# -- tier arithmetic -----------------------------------------------------


def test_risk_tiers_are_ordered_by_severity():
    assert RISK_TIERS == ("T0", "T1", "T2", "T3")


@pytest.mark.parametrize(
    "tier,threshold,expected",
    [
        ("T0", "T1", False),
        ("T1", "T1", True),
        ("T2", "T1", True),
        ("T3", "T2", True),
        ("T1", "T2", False),
    ],
)
def test_risk_at_least(tier, threshold, expected):
    assert risk_at_least(tier, threshold) is expected


def test_unknown_tier_never_satisfies_a_threshold():
    """An unrecognised tier must not be read as "low risk"."""
    assert risk_at_least("T9", "T1") is False


@pytest.mark.parametrize(
    "tiers,expected",
    [([], "T0"), (["T0"], "T0"), (["T0", "T2", "T1"], "T2"), (["T1", "T3"], "T3")],
)
def test_highest_risk_tier(tiers, expected):
    assert highest_risk_tier(tiers) == expected


def test_highest_risk_tier_ignores_unknown_values():
    assert highest_risk_tier(["bogus", "T1"]) == "T1"
