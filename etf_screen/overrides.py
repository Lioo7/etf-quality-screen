"""Manual field overrides — a user-maintained escape hatch.

When the provider is missing or wrong about a value (a real SBC figure you looked
up, say), you can supply verified numbers in a **git-ignored** ``overrides.json``
keyed by ticker. Overrides take precedence over the provider, and any name they
touch is flagged ``manual override`` so it is never mistaken for raw feed data.

Format (all fields optional except when rescuing a name the provider skipped, in
which case every required field must be present)::

    {
      "CEG": { "name": "Constellation Energy", "sbc_ttm": 120000000 },
      "FOO": {
        "name": "Foo Corp", "revenue_ttm": 5000, "revenue_ttm_prior": 4000,
        "ocf_ttm": 1400, "capex_ttm": 200, "sbc_ttm": 100,
        "diluted_shares_now": 800, "diluted_shares_prior": 790,
        "market_cap": 60000, "forward_pe": 25, "forward_eps_growth": 20,
        "net_income_ttm": 900
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .screen import Company

DEFAULT_PATH = "overrides.json"

# Current-snapshot balance-sheet lines a user may patch. They are NOT required to
# rescue a name (Company supplies gate-passing defaults), so a sparse override
# still validates while still being able to correct any single balance-sheet line.
_BALANCE_SHEET = {
    "total_debt", "cash_and_equivalents", "goodwill", "total_assets",
    "operating_income", "total_equity",
}
# Fields a user may override / supply. Scalar snapshot lines only — annual
# `history` is intentionally not overridable here, so an override-rescued name
# carries no history and lands in INSUFFICIENT_HISTORY when the gate runs.
_OVERRIDABLE = {
    "name", "revenue_ttm", "revenue_ttm_prior", "ocf_ttm", "capex_ttm",
    "sbc_ttm", "diluted_shares_now", "diluted_shares_prior", "market_cap",
    "forward_pe", "forward_eps_growth", "net_income_ttm",
} | _BALANCE_SHEET
# Fields that must all be present to construct a Company from scratch (the
# balance-sheet lines are excluded — they fall back to Company defaults).
_REQUIRED = _OVERRIDABLE - {"name", "forward_pe"} - _BALANCE_SHEET


def load_overrides(path: str | Path = DEFAULT_PATH) -> dict[str, dict]:
    """Load ticker->fields overrides from ``path``; return {} if it doesn't exist."""
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    return {k.upper(): v for k, v in data.items()}


def apply_override(company: Company, fields: dict) -> Company:
    """Return a copy of ``company`` with ``fields`` applied and flagged."""
    changes = {k: v for k, v in fields.items() if k in _OVERRIDABLE}
    if not changes:
        return company
    updated = replace(company, **changes)
    # replace() aliases the mutable lists — copy them before mutating.
    updated.low_confidence = list(company.low_confidence)
    updated.overridden_fields = sorted(set(company.overridden_fields) | set(changes))
    # A supplied SBC means it is no longer an assumed zero; drop the stale flag.
    if "sbc_ttm" in changes:
        updated.sbc_assumed_zero = False
        updated.low_confidence = [m for m in updated.low_confidence
                                  if "assumed 0" not in m]
    return updated


def company_from_override(ticker: str, fields: dict) -> Company | None:
    """Build a Company purely from an override, or None if it's incomplete.

    Used to rescue a name the provider skipped entirely, when the override
    carries every required field.
    """
    if not _REQUIRED.issubset(fields):
        return None
    numeric = {k: fields[k] for k in _REQUIRED}
    # Honor any supplied balance-sheet lines; the rest fall back to Company defaults.
    extra = {k: fields[k] for k in _BALANCE_SHEET if k in fields}
    company = Company(
        ticker=ticker, forward_pe=fields.get("forward_pe"),
        basis="override", **numeric, **extra,
    )
    supplied = set(numeric) | set(extra)
    if "forward_pe" in fields:
        supplied.add("forward_pe")
    if "name" in fields:
        company.name = fields["name"]
        supplied.add("name")
    company.overridden_fields = sorted(supplied)
    return company
