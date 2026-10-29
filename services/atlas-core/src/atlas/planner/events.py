"""Builders for the four events this workstream emits.

OWNER: Person 2 - Voice, Intent & 9Router.

``INSTRUCTIONS.md`` section 5 - provides to Person 3: ``transcript``,
``plan_ready``, ``clarification_required``, ``provider_error``. The envelope and
payload shapes come from ``packages/contracts/events.schema.json``
(project-owner owned, read-only here).

Why typed builders rather than dict literals at each call site:

* ``schemaVersion`` is the schema's ``const "1.0"`` - one place to change.
* ``timestamp`` is RFC 3339 UTC with a ``Z`` suffix, which is what
  ``"format": "date-time"`` expects and what the example files show.
  ``datetime.isoformat()`` alone would emit ``+00:00``.
* Every payload is passed through :func:`atlas.providers.redaction.redact_object`
  before it leaves, because the schema states events "must never contain API
  keys, passwords, or unnecessary private content" and a single forgotten call
  site would break that promise.

The orchestrator (project owner) owns emission, ordering, and the SSE stream;
these builders only produce envelopes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..providers.base import ProviderError
from ..providers.redaction import redact_object
from .base import PlanOutcome, PlanOutcomeKind

#: ``events.schema.json`` pins this with ``"const": "1.0"``.
SCHEMA_VERSION = "1.0"

EVENT_TRANSCRIPT = "transcript"
EVENT_PLAN_READY = "plan_ready"
EVENT_CLARIFICATION_REQUIRED = "clarification_required"
EVENT_CONFIRMATION_REQUIRED = "confirmation_required"
EVENT_PROVIDER_ERROR = "provider_error"


def utc_timestamp(moment: datetime | None = None) -> str:
    """RFC 3339 UTC timestamp with a ``Z`` suffix."""
    now = moment or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_event(
    task_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build one redacted :class:`AtlasEvent` envelope."""
    if not task_id:
        raise ValueError("taskId is required and must be non-empty (events.schema.json)")
    redacted = redact_object(payload)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "taskId": task_id,
        "timestamp": timestamp or utc_timestamp(),
        "type": event_type,
        "payload": redacted if isinstance(redacted, dict) else {},
    }


def transcript_event(
    task_id: str,
    text: str,
    *,
    is_final: bool,
    source: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """``transcript`` - what ATLAS believes the user asked for.

    Emitted for typed input too (``source="text"``), so the UI has one place to
    show the recognized request (Master PRD 8.2 step 2).
    """
    if source not in ("voice", "text"):
        raise ValueError(f"source must be 'voice' or 'text', got {source!r}")
    return build_event(
        task_id,
        EVENT_TRANSCRIPT,
        {"text": text, "isFinal": bool(is_final), "source": source},
        timestamp=timestamp,
    )


def plan_ready_event(
    task_id: str,
    plan: dict[str, Any],
    plan_digest: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """``plan_ready`` - the exact plan the user is about to review.

    ``planDigest`` must be echoed by ``POST /confirm``, which is what binds
    approval to this plan and no other (``docs/api-contract.md``).
    """
    if not plan_digest:
        raise ValueError("planDigest is required; confirmation cannot be bound without it")
    return build_event(
        task_id,
        EVENT_PLAN_READY,
        {"plan": plan, "planDigest": plan_digest},
        timestamp=timestamp,
    )


def confirmation_required_event(
    task_id: str,
    plan_digest: str,
    highest_risk_tier: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """``confirmation_required`` - consent is needed before execution.

    Emitted by this workstream for completeness of the planning path; the
    orchestrator decides whether it, ``plan_ready``, or both are sent.
    """
    if highest_risk_tier not in ("T0", "T1", "T2", "T3"):
        raise ValueError(f"highestRiskTier must be T0..T3, got {highest_risk_tier!r}")
    return build_event(
        task_id,
        EVENT_CONFIRMATION_REQUIRED,
        {"planDigest": plan_digest, "highestRiskTier": highest_risk_tier},
        timestamp=timestamp,
    )


def clarification_required_event(
    task_id: str,
    question: str,
    options: tuple[str, ...] | list[str] = (),
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """``clarification_required`` - ATLAS is asking rather than guessing."""
    if not question or not question.strip():
        raise ValueError("clarification_required needs a non-empty question")
    payload: dict[str, Any] = {"question": question.strip()}
    if options:
        payload["options"] = [str(option) for option in options]
    return build_event(task_id, EVENT_CLARIFICATION_REQUIRED, payload, timestamp=timestamp)


def provider_error_event(
    task_id: str,
    error: ProviderError,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """``provider_error`` - a named, redacted provider failure.

    The payload is produced by :meth:`ProviderError.to_event_payload`, whose
    ``message`` was already redacted when the error was constructed.
    """
    return build_event(task_id, EVENT_PROVIDER_ERROR, error.to_event_payload(), timestamp=timestamp)


def events_for_outcome(
    task_id: str,
    outcome: PlanOutcome,
    *,
    timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Map a :class:`PlanOutcome` onto the events it implies.

    A ``PLAN`` outcome yields ``plan_ready``, plus ``confirmation_required``
    when consent is needed. A ``CLARIFICATION`` yields
    ``clarification_required``. A ``REJECTED`` outcome yields
    ``provider_error`` only when a provider actually failed - a plan rejected
    by local validation is not a provider error, and reporting it as one would
    misdirect the user and the defect report.

    Terminal failure events (``task_failed``) are intentionally not built here:
    task lifecycle belongs to the orchestrator and Person 1's runtime.
    """
    if outcome.kind is PlanOutcomeKind.PLAN:
        if outcome.plan is None:
            raise ValueError("PLAN outcome without a plan")
        events = [plan_ready_event(task_id, outcome.plan, outcome.plan_digest, timestamp=timestamp)]
        if outcome.requires_confirmation:
            events.append(
                confirmation_required_event(
                    task_id,
                    outcome.plan_digest,
                    outcome.highest_risk_tier,
                    timestamp=timestamp,
                )
            )
        return events

    if outcome.kind is PlanOutcomeKind.CLARIFICATION:
        return [
            clarification_required_event(
                task_id,
                outcome.clarification_question,
                outcome.clarification_options,
                timestamp=timestamp,
            )
        ]

    if outcome.provider_error is not None:
        return [provider_error_event(task_id, outcome.provider_error, timestamp=timestamp)]
    return []
