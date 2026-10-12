"""Detect spoken email patterns and collect reconstruction candidates for LLM correction.

Detects dictated email patterns like "john at company dot com" and reconstructs
them as candidates for the LLM corrector. If CRM contact emails are available,
fuzzy-matches against them to prefer known emails.
"""

import logging
import re
from typing import List, Optional

import jellyfish

logger = logging.getLogger(__name__)

_DICTATED = re.compile(
    r'\b(\w+(?:\s+dot\s+\w+|[.\-]\w+)*)\s+'
    r'(?:at|at the rate|at sign|@)\s+'
    r'(\w+(?:\s+dot\s+\w+|[\-]\w+)*)'
    r'(?:\s+dot\s+|\.)'
    r'(com|io|org|net|ai|co|in|dev|app|tech|cloud|info|biz|us|uk|de|fr|au|ca|edu|gov|eu)\b',
    re.IGNORECASE,
)


def _clean_local_part(local: str) -> str:
    """Convert spoken dots to actual dots: 'john dot smith' → 'john.smith'"""
    return re.sub(r'\s+dot\s+', '.', local, flags=re.IGNORECASE).lower()



def _reconstruct_email(local: str, domain: str, tld: str) -> str:
    clean_local = _clean_local_part(local)
    return f"{clean_local}@{domain.lower()}.{tld.lower()}"


def _match_crm_email(
    reconstructed: str, known_emails: List[str], jw_threshold: float = 0.70,
) -> Optional[str]:
    if not known_emails:
        return None
    r_local, r_domain = reconstructed.split("@", 1)
    best_match = None
    best_score = 0.0
    for email in known_emails:
        if "@" not in email:
            continue
        k_local, k_domain = email.lower().split("@", 1)
        if k_domain != r_domain:
            continue
        score = jellyfish.jaro_winkler_similarity(r_local, k_local)
        if score >= jw_threshold and score > best_score:
            best_score = score
            best_match = email
    return best_match


def collect_email_candidates(
    segments: List[dict],
    known_emails: Optional[List[str]] = None,
) -> List[dict]:
    """Scan transcript for spoken email patterns and return reconstruction candidates."""
    candidates = []
    known = known_emails or []

    for seg_idx, seg in enumerate(segments):
        text = seg.get("text", "")
        if not text:
            continue

        for m_idx, match in enumerate(_DICTATED.finditer(text)):
            local, domain, tld = match.group(1), match.group(2), match.group(3)
            reconstructed = _reconstruct_email(local, domain, tld)

            crm_match = _match_crm_email(reconstructed, known)
            best_candidate = crm_match if crm_match else reconstructed

            matched_span = match.group(0)
            word_count = len(matched_span.split())

            context_start = max(0, match.start() - 60)
            context_end = min(len(text), match.end() + 60)
            context = text[context_start:context_end]

            crm_hint = ""
            if known:
                domain_emails = [e for e in known if e.lower().endswith(f"@{domain.lower()}.{tld.lower()}")]
                if domain_emails:
                    crm_hint = f" Known emails at this domain: {', '.join(domain_emails[:5])}"

            candidates.append({
                "word_id": f"seg{seg_idx}_email_{m_idx}",
                "word": matched_span,
                "score": 0.0,
                "phonetic_candidates": [best_candidate],
                "context": context + crm_hint,
                "field": "text",
                "segment_index": seg_idx,
                "word_index": len(text[:match.start()].split()),
                "match_type": "email_dictated",
                "span_length": word_count,
            })

    if candidates:
        logger.info(f"Email detector: found {len(candidates)} email candidates")
    return candidates
