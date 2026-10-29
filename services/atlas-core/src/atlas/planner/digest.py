"""Canonical plan digest - binds a confirmation to the exact reviewed plan.

OWNER: Person 2 - Voice, Intent & 9Router.

``docs/api-contract.md``: "Confirmation applies to the exact plan/version
reviewed (``planDigest``). A plan is never silently replaced between review and
execution." ``POST /api/v1/tasks/{taskId}/confirm`` echoes the digest from the
``plan_ready``/``confirmation_required`` event and is rejected if the plan
changed.

For that rule to hold, the digest must depend on every byte of the plan that
could change its meaning, and must not depend on anything that cannot. Hence
canonical JSON: sorted keys, no insignificant whitespace, UTF-8, and
``ensure_ascii=False`` so the same text always produces the same bytes.

Scope: computing and comparing the digest is this module's job. *Enforcing* the
comparison at confirmation time belongs to the orchestrator/API (project owner)
and the policy engine (Person 1); :func:`verify_digest` is provided so neither
has to reimplement it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

#: Prefix recorded in the digest string. ``examples/event.plan_ready.json``
#: uses ``"sha256:..."``, so the algorithm stays visible in the wire format and
#: can be migrated without ambiguity.
DIGEST_PREFIX = "sha256:"


def canonical_json(plan: Any) -> str:
    """Return the canonical JSON text used as digest input."""
    return json.dumps(
        plan,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def compute_plan_digest(plan: Any) -> str:
    """Return ``sha256:<hex>`` over the canonical form of ``plan``."""
    payload = canonical_json(plan).encode("utf-8")
    return DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def verify_digest(plan: Any, expected_digest: str) -> bool:
    """Constant-time check that ``plan`` still matches ``expected_digest``.

    ``hmac.compare_digest`` is used rather than ``==``. The digest is not a
    secret, but comparing it in constant time costs nothing and removes any
    question about leaking information through timing at the confirmation
    boundary.
    """
    if not expected_digest:
        return False
    return hmac.compare_digest(compute_plan_digest(plan), expected_digest.strip())
