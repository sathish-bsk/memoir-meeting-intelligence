"""
Helpers to transform raw analysis.json memoir data into structured response fields.

Extracted from read_endpoints.py so they can be reused by the public share endpoint.
"""

from typing import Any


def transform_discovery_fields(raw_fields: Any) -> list | None:
    """Transform raw discovery_fields dict → list of {name, value, confidence, speaker, quote}."""
    if isinstance(raw_fields, dict):
        discovery_fields = []
        for field_name, field_data in raw_fields.items():
            if field_data is None:
                continue
            if isinstance(field_data, dict):
                val = field_data.get("value")
                if val is None:
                    for key in ("primary_pain", "target_date", "decision_maker",
                                "current_tools", "size", "roles",
                                "competitors_evaluating", "pain_with_current",
                                "approval_process", "driving_event"):
                        candidate = field_data.get(key)
                        if candidate:
                            if isinstance(candidate, list):
                                val = ", ".join(str(c) for c in candidate) if candidate else None
                            else:
                                val = str(candidate)
                            break
                if val is None:
                    continue
                discovery_fields.append({
                    "name": field_name,
                    "value": str(val),
                    "confidence": field_data.get("confidence", "inferred"),
                    "speaker": field_data.get("source_speaker", field_data.get("speaker", "")),
                    "quote": field_data.get("source_quote", field_data.get("quote", "")) or "",
                })
            elif isinstance(field_data, str) and field_data:
                discovery_fields.append({
                    "name": field_name,
                    "value": field_data,
                    "confidence": "inferred",
                    "speaker": "",
                    "quote": "",
                })
        return discovery_fields
    elif isinstance(raw_fields, list):
        return raw_fields
    return None


def transform_action_items(raw_actions: Any) -> list | None:
    """Transform action_items {rep_actions, prospect_commitments} → flat list of {text, type}."""
    if isinstance(raw_actions, dict):
        action_items = []
        for a in raw_actions.get("rep_actions", []):
            text = a.get("action", "") if isinstance(a, dict) else str(a)
            if text:
                action_items.append({"text": text, "type": "rep"})
        for a in raw_actions.get("prospect_commitments", []):
            text = a.get("action", "") if isinstance(a, dict) else str(a)
            if text:
                action_items.append({"text": text, "type": "prospect"})
        return action_items
    elif isinstance(raw_actions, list):
        return raw_actions
    return None


def transform_signals(raw_signals: Any) -> dict | None:
    """Transform signals {buying_signals, risk_signals} → {buying, risk} as flat string lists."""
    if isinstance(raw_signals, dict):
        raw_buying = raw_signals.get("buying_signals") or raw_signals.get("buying") or []
        raw_risk = raw_signals.get("risk_signals") or raw_signals.get("risk") or []
        buying = [
            (s.get("signal", str(s)) if isinstance(s, dict) else str(s))
            for s in raw_buying
        ]
        risk = [
            (s.get("signal", str(s)) if isinstance(s, dict) else str(s))
            for s in raw_risk
        ]
        result: dict = {"buying": buying, "risk": risk}
        if raw_signals.get("discussion_threads"):
            result["discussion_threads"] = raw_signals["discussion_threads"]
        return result
    elif isinstance(raw_signals, list):
        return raw_signals
    return None


def transform_disclaimer(raw_disclaimer: Any) -> str | None:
    """Transform disclaimer: may be dict or string."""
    if isinstance(raw_disclaimer, str):
        return raw_disclaimer
    elif isinstance(raw_disclaimer, dict):
        topics = raw_disclaimer.get("ambiguous_topics", [])
        if topics:
            return "; ".join(
                f"{t.get('topic', '')}: {t.get('reason', '')}" if isinstance(t, dict) else str(t)
                for t in topics
            )
    return None
