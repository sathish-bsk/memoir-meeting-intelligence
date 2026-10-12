"""
Plan tier definitions and limit resolution.
"""

import json
from typing import Any, Dict, Optional


# -1 means unlimited
DEFAULT_PLAN_LIMITS: Dict[str, Dict[str, int]] = {
    "trial": {
        "calls_per_month": 50,
        "team_members": 10,
        "storage_gb": 5,
        "intel_reports_per_month": -1,
    },
    "free": {
        "calls_per_month": 10,
        "team_members": 3,
        "storage_gb": 1,
        "intel_reports_per_month": 3,
    },
    "paid": {
        "calls_per_month": 200,
        "team_members": 50,
        "storage_gb": 50,
        "intel_reports_per_month": -1,
    },
}

VALID_PLANS = set(DEFAULT_PLAN_LIMITS.keys())


def get_plan_limits(plan: str, overrides: Optional[str] = None) -> Dict[str, int]:
    """Resolve plan limits, merging defaults with per-team JSON overrides.

    Args:
        plan: The plan name (trial, free, paid).
        overrides: Optional JSON string of per-team limit overrides.

    Returns:
        Dict of limit_name -> value. -1 means unlimited.
    """
    defaults = DEFAULT_PLAN_LIMITS.get(plan, DEFAULT_PLAN_LIMITS["free"]).copy()

    if overrides:
        try:
            custom = json.loads(overrides)
            if isinstance(custom, dict):
                for key, value in custom.items():
                    if key in defaults and isinstance(value, int):
                        defaults[key] = value
        except (json.JSONDecodeError, TypeError):
            pass

    return defaults
