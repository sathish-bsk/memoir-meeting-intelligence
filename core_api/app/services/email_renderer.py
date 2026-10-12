"""
Branded HTML email renderer for meeting summaries.

Builds complete inline-CSS HTML email bodies for three audiences:
  - Recorder (full summary with all sections)
  - Internal team member (filtered action items, key decisions)
  - External participant (minimal branding, their items only)
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse


def _settings_url_from(call_url: str) -> str:
    """Derive the user's notification-settings URL from any meeting-detail URL.

    Replaces an earlier `.rsplit('/calls/', 1)[0]` parser that broke the moment
    the meeting-detail route was renamed (May 2026 /calls → /meetings). Using
    urlparse means the renderer survives future route renames without code change.
    """
    parsed = urlparse(call_url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    return f"{base}/settings/notifications"

# ── Color tokens ──────────────────────────────────────────────────────────────

BRAND = "#6C5CE7"
BRAND_DARK = "#5647C4"
BRAND_LIGHT = "#EDE9FD"
BRAND_ULTRA_LIGHT = "#F7F5FF"

BG_PAGE = "#FAFAF8"
BG_SURFACE = "#FFFFFF"
BORDER = "#E8E6E1"
BORDER_LIGHT = "#F0EEEA"

TEXT_PRIMARY = "#1A1A18"
TEXT_SECONDARY = "#6B6B66"
TEXT_TERTIARY = "#9C9C96"

DECISION_BG = "#EDF7ED"
DECISION_BORDER = "#B8D4B8"
DECISION_TEXT = "#2D5A2D"

OPEN_BG = "#FFF8ED"
OPEN_BORDER = "#E0CC8A"
OPEN_TEXT = "#7A6520"

URGENT = "#E04B2F"
THIS_WEEK = "#D4890A"
UPCOMING = "#9C9C96"

FONT_STACK = "'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
MONO_STACK = "'SF Mono', 'Cascadia Code', Consolas, monospace"

_URGENCY_COLORS = {
    "today": URGENT,
    "this_week": THIS_WEEK,
    "this week": THIS_WEEK,
    "next_week": THIS_WEEK,
    "next week": THIS_WEEK,
    "upcoming": UPCOMING,
    "async": UPCOMING,
}

_URGENCY_LABELS = {
    "today": "Today",
    "this_week": "This week",
    "this week": "This week",
    "next_week": "Next week",
    "next week": "Next week",
    "upcoming": "Upcoming",
    "async": "Async",
}

_URGENCY_ORDER = {
    "today": 0,
    "this_week": 1,
    "this week": 1,
    "next_week": 2,
    "next week": 2,
    "upcoming": 3,
    "async": 4,
}

MEMOIR_LOGO_SVG = (
    '<table cellpadding="0" cellspacing="0" border="0"><tr>'
    '<td style="vertical-align:middle;">'
    '<img src="https://app.meetmemoir.com/logo-icon.svg" '
    'width="32" height="32" alt="Memoir" style="display:block;border-radius:8px;" />'
    "</td>"
    f'<td style="padding-left:8px;font-size:20px;font-weight:700;color:{TEXT_PRIMARY};'
    f"letter-spacing:-0.5px;font-family:{FONT_STACK};vertical-align:middle;\">Memoir</td>"
    "</tr></table>"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _e(text: str | None) -> str:
    """Escape user-provided text for safe HTML rendering."""
    if text is None:
        return ""
    return html.escape(str(text))


def _format_duration(seconds: int | float | None) -> str:
    """Format seconds into human-readable duration: '32 min' or '1h 15m'."""
    if not seconds:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours == 0:
        return f"{minutes} min"
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes}m"


def _format_date(iso_date: str | None) -> str:
    """Format ISO date string into 'APR 16, 2026'."""
    if not iso_date:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_date).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y").upper().replace(" 0", " ")
    except (ValueError, TypeError):
        return str(iso_date)


def _format_platform(platform: str | None) -> str:
    """Format platform/recording source into a display-friendly label."""
    if not platform:
        return ""
    _PLATFORM_MAP = {
        "local": "Uploaded",
        "upload": "Uploaded",
        "google_meet": "Google Meet",
        "meet.google.com": "Google Meet",
        "zoom": "Zoom",
        "zoom.us": "Zoom",
        "teams": "Microsoft Teams",
        "teams.microsoft.com": "Microsoft Teams",
        "teams.live.com": "Microsoft Teams",
        "bot": "Bot Recording",
    }
    if not platform:
        return ""
    lower = platform.lower().strip()
    if lower in _PLATFORM_MAP:
        return _PLATFORM_MAP[lower]
    return platform.replace("_", " ").title()


def _wrap_email(body_html: str) -> str:
    """Wrap rendered body in a complete HTML document with inline styles."""
    return (
        '<!DOCTYPE html>\n'
        '<html lang="en" xmlns="http://www.w3.org/1999/xhtml">\n'
        "<head>\n"
        '  <meta charset="UTF-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        '  <meta http-equiv="X-UA-Compatible" content="IE=edge" />\n'
        "  <title>Meeting Summary</title>\n"
        "  <!--[if mso]>\n"
        "  <noscript><xml>\n"
        "    <o:OfficeDocumentSettings>\n"
        "      <o:PixelsPerInch>96</o:PixelsPerInch>\n"
        "    </o:OfficeDocumentSettings>\n"
        "  </xml></noscript>\n"
        "  <![endif]-->\n"
        "</head>\n"
        f'<body style="margin:0;padding:0;background:{BG_PAGE};font-family:{FONT_STACK};'
        f'color:{TEXT_PRIMARY};line-height:1.6;-webkit-font-smoothing:antialiased;">\n'
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_PAGE};">\n'
        '<tr><td align="center" style="padding:24px 0 0 0;">\n'
        f'<table cellpadding="0" cellspacing="0" border="0" width="660" '
        f'style="max-width:660px;width:100%;">\n'
        f"<tr><td>\n{body_html}\n</td></tr>\n"
        "</table>\n"
        "</td></tr>\n"
        "</table>\n"
        "</body>\n"
        "</html>"
    )


# ── Section Builders ──────────────────────────────────────────────────────────

def _render_header(
    title: str,
    date_str: str | None,
    duration_seconds: int | float | None,
    platform: str | None,
    participant_count: int | None,
    org_name: str | None,
    recorder_name: str | None,
) -> str:
    date_formatted = _format_date(date_str)
    duration = _format_duration(duration_seconds)
    meta_right = " &middot; ".join(filter(None, [date_formatted, duration]))

    context_parts: list[str] = []
    if platform:
        context_parts.append(_e(_format_platform(platform)))
    if participant_count:
        context_parts.append(f"{participant_count} participants")
    if org_name:
        context_parts.append(f"Internal &mdash; {_e(org_name)}")
    if recorder_name:
        context_parts.append(_e(recorder_name))

    sep = (
        f'<td style="padding:0 8px;">'
        f'<span style="display:inline-block;width:4px;height:4px;border-radius:50%;'
        f'background:{TEXT_TERTIARY};vertical-align:middle;"></span></td>'
    )
    context_cells = sep.join(
        f'<td style="font-size:14px;color:{TEXT_SECONDARY};font-family:{FONT_STACK};">'
        f"{p}</td>"
        for p in context_parts
    )

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_SURFACE};border-bottom:1px solid {BORDER_LIGHT};">\n'
        f'<tr><td style="padding:32px 20px 28px 20px;">\n'
        # Top row: logo left, date right
        f'  <table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="margin-bottom:20px;">\n'
        f"  <tr>\n"
        f'    <td style="vertical-align:middle;">{MEMOIR_LOGO_SVG}</td>\n'
        f'    <td align="right" style="vertical-align:middle;font-size:13px;'
        f"color:{TEXT_TERTIARY};font-family:{MONO_STACK};\">{meta_right}</td>\n"
        f"  </tr>\n"
        f"  </table>\n"
        # Title
        f'  <h1 style="margin:0 0 12px 0;font-size:26px;font-weight:700;'
        f"letter-spacing:-0.5px;line-height:1.25;color:{TEXT_PRIMARY};"
        f'font-family:{FONT_STACK};">{_e(title)}</h1>\n'
        # Context line
        f'  <table cellpadding="0" cellspacing="0" border="0"><tr>{context_cells}'
        f"</tr></table>\n"
        f"</td></tr>\n"
        f"</table>\n"
    )


def _render_tldr(tldr_text: str | None) -> str:
    if not tldr_text:
        return ""
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="margin:28px 0;">\n'
        f'<tr><td style="background:{BG_SURFACE};border:1px solid {BORDER};'
        f"border-left:3px solid {BRAND};border-radius:0 8px 8px 0;"
        f'padding:24px 28px;">\n'
        f'  <div style="font-family:{MONO_STACK};font-size:11px;font-weight:600;'
        f"text-transform:uppercase;letter-spacing:1.5px;color:{BRAND};"
        f'margin-bottom:10px;">30-Second Summary</div>\n'
        f'  <p style="margin:0;font-size:15px;line-height:1.65;color:{TEXT_PRIMARY};'
        f'font-family:{FONT_STACK};">{_e(tldr_text)}</p>\n'
        f"</td></tr>\n"
        f"</table>\n"
    )


def _render_decisions(decisions: list[dict] | None) -> str:
    if not decisions:
        return ""
    items_html = ""
    for i, d in enumerate(decisions):
        mb = "0" if i == len(decisions) - 1 else "10px"
        items_html += (
            f'<div style="padding:14px 16px;background:{DECISION_BG};border-radius:6px;'
            f"margin-bottom:{mb};border:1px solid {DECISION_BORDER};\">\n"
            f'  <div style="font-weight:600;font-size:14px;color:{DECISION_TEXT};'
            f'margin-bottom:4px;">{_e(d.get("title", ""))}</div>\n'
            f'  <div style="font-size:13px;color:{TEXT_SECONDARY};line-height:1.55;">'
            f'{_e(d.get("detail", ""))}</div>\n'
            f"</div>\n"
        )
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_SURFACE};border:1px solid {BORDER};border-radius:8px;'
        f'margin-bottom:20px;overflow:hidden;">\n'
        # Header
        f'<tr><td style="padding:16px 24px;border-bottom:1px solid {BORDER_LIGHT};'
        f'font-weight:600;font-size:14px;color:{DECISION_TEXT};font-family:{FONT_STACK};">\n'
        f'  <table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="width:26px;height:26px;background:{DECISION_BG};border-radius:6px;'
        f'text-align:center;vertical-align:middle;font-size:13px;">&#10003;</td>'
        f'<td style="padding-left:10px;color:{DECISION_TEXT};font-weight:600;'
        f'font-size:14px;">Decisions Made</td>'
        f"</tr></table>\n"
        f"</td></tr>\n"
        # Body
        f'<tr><td style="padding:16px 24px;">\n{items_html}</td></tr>\n'
        f"</table>\n"
    )


def _render_open_questions(questions: list[dict] | None) -> str:
    if not questions:
        return ""
    items_html = ""
    for i, q in enumerate(questions):
        text = q.get("text", "") if isinstance(q, dict) else str(q)
        mb = "0" if i == len(questions) - 1 else "8px"
        items_html += (
            f'<div style="padding:12px 16px;background:{OPEN_BG};'
            f"border:1px solid {OPEN_BORDER};border-radius:6px;margin-bottom:{mb};"
            f"font-size:13px;color:{OPEN_TEXT};line-height:1.5;"
            f'font-family:{FONT_STACK};">{_e(text)}</div>\n'
        )
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_SURFACE};border:1px solid {BORDER};border-radius:8px;'
        f'margin-bottom:20px;overflow:hidden;">\n'
        # Header
        f'<tr><td style="padding:16px 24px;border-bottom:1px solid {BORDER_LIGHT};'
        f'font-weight:600;font-size:14px;color:{OPEN_TEXT};font-family:{FONT_STACK};">\n'
        f'  <table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="width:26px;height:26px;background:{OPEN_BG};border-radius:6px;'
        f'text-align:center;vertical-align:middle;font-size:13px;">?</td>'
        f'<td style="padding-left:10px;color:{OPEN_TEXT};font-weight:600;'
        f'font-size:14px;">Still Unresolved</td>'
        f"</tr></table>\n"
        f"</td></tr>\n"
        # Body
        f'<tr><td style="padding:16px 24px;">\n{items_html}</td></tr>\n'
        f"</table>\n"
    )


def _render_action_items(
    items: list[dict] | None,
    filter_email: str | None = None,
    show_team_count: bool = False,
) -> str:
    if items is None:
        items = []

    # Count owners and items
    owners = set()
    for it in items:
        owner = it.get("owner", "")
        if owner:
            owners.add(owner)

    count_text = ""
    if items:
        count_text = f"{len(items)} items &middot; {len(owners)} owners"

    # Sort by urgency
    sorted_items = sorted(
        items, key=lambda x: _URGENCY_ORDER.get(x.get("urgency", "upcoming"), 99)
    )

    # Determine which items belong to filter_email recipient
    recipient_count = 0
    if filter_email:
        for it in sorted_items:
            if (not filter_email or not it.get("owner_email") or (it.get("owner_email") or "").lower() == filter_email.lower()):
                recipient_count += 1

    rows_html = ""
    if not sorted_items:
        rows_html = (
            f'<tr><td colspan="2" style="padding:14px 0;font-size:14px;'
            f'color:{TEXT_SECONDARY};font-style:italic;">'
            f"No action items identified for this meeting.</td></tr>\n"
        )
    else:
        for i, it in enumerate(sorted_items):
            owner = _e(it.get("owner", ""))
            urgency = it.get("urgency", "upcoming").lower()
            color = _URGENCY_COLORS.get(urgency, UPCOMING)
            label = _URGENCY_LABELS.get(urgency, "Upcoming")
            text = _e(it.get("text", ""))
            context = it.get("context", "")

            is_recipient = (
                filter_email
                and (not filter_email or not it.get("owner_email") or (it.get("owner_email") or "").lower() == filter_email.lower())
            )
            highlight_bg = BRAND_ULTRA_LIGHT if is_recipient else "transparent"
            highlight_border = f"border-left:3px solid {BRAND};" if is_recipient else ""

            border_bottom = (
                f"border-bottom:1px solid {BORDER_LIGHT};"
                if i < len(sorted_items) - 1
                else ""
            )

            context_html = ""
            if context:
                context_html = (
                    f'<div style="font-size:12px;color:{TEXT_TERTIARY};margin-top:3px;'
                    f'font-style:italic;font-family:{FONT_STACK};">{_e(context)}</div>'
                )

            rows_html += (
                f"<tr>\n"
                f'  <td style="padding:14px 12px 14px 16px;vertical-align:top;'
                f"width:100px;{border_bottom}{highlight_border}"
                f'background:{highlight_bg};">\n'
                f'    <div style="font-weight:600;font-size:13px;color:{TEXT_PRIMARY};'
                f'font-family:{FONT_STACK};">{owner}</div>\n'
                f'    <div style="margin-top:4px;font-family:{MONO_STACK};'
                f"font-size:10px;font-weight:600;text-transform:uppercase;"
                f'letter-spacing:0.3px;color:{color};">'
                f'<span style="display:inline-block;width:6px;height:6px;'
                f'border-radius:50%;background:{color};vertical-align:middle;'
                f'margin-right:4px;"></span>{label}</div>\n'
                f"  </td>\n"
                f'  <td style="padding:14px 16px 14px 12px;vertical-align:top;'
                f'{border_bottom}background:{highlight_bg};">\n'
                f'    <div style="font-size:14px;line-height:1.5;color:{TEXT_PRIMARY};'
                f'font-family:{FONT_STACK};">{text}</div>\n'
                f"    {context_html}\n"
                f"  </td>\n"
                f"</tr>\n"
            )

    team_note = ""
    if show_team_count and filter_email and items:
        other_count = len(items) - recipient_count
        if other_count > 0:
            team_note = (
                f'<tr><td colspan="2" style="padding:12px 16px;font-size:13px;'
                f"color:{TEXT_SECONDARY};font-style:italic;border-top:1px solid "
                f'{BORDER_LIGHT};">{other_count} other action items assigned to the '
                f"team &mdash; view in Memoir</td></tr>\n"
            )

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_SURFACE};border:1px solid {BORDER};border-radius:8px;'
        f'margin-bottom:20px;overflow:hidden;">\n'
        # Header
        f'<tr><td style="padding:16px 24px;border-bottom:1px solid {BORDER_LIGHT};'
        f'font-family:{FONT_STACK};">\n'
        f'  <table cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
        f'<td style="width:26px;height:26px;background:{BRAND_LIGHT};border-radius:6px;'
        f'text-align:center;vertical-align:middle;font-size:13px;color:{BRAND_DARK};">'
        f"&rarr;</td>"
        f'<td style="padding-left:10px;color:{BRAND_DARK};font-weight:600;'
        f'font-size:14px;">Action Items</td>'
        f'<td align="right" style="font-size:12px;color:{TEXT_TERTIARY};'
        f'font-weight:400;">{count_text}</td>'
        f"</tr></table>\n"
        f"</td></tr>\n"
        # Body — table layout for Outlook
        f'<tr><td style="padding:0 24px;">\n'
        f'  <table cellpadding="0" cellspacing="0" border="0" width="100%">\n'
        f"  {rows_html}{team_note}"
        f"  </table>\n"
        f"</td></tr>\n"
        f"</table>\n"
    )


def _render_discussion_threads(threads: list[dict] | None) -> str:
    if not threads:
        return ""
    display = threads[:6]

    priority_colors = {
        "high": BRAND,
        "medium": THIS_WEEK,
        "low": UPCOMING,
    }

    items_html = ""
    for i, t in enumerate(display):
        priority = t.get("priority", "low").lower()
        bar_color = priority_colors.get(priority, UPCOMING)
        title = _e(t.get("title", ""))
        summary = _e(t.get("summary", ""))
        border_bottom = (
            f"border-bottom:1px solid {BORDER_LIGHT};"
            if i < len(display) - 1
            else ""
        )

        attribution_html = ""
        attributions = t.get("attributions", [])
        if attributions:
            parts = []
            for a in attributions:
                speaker = _e(a.get("speaker", ""))
                action = _e(a.get("action", ""))
                parts.append(
                    f'<strong style="color:{TEXT_PRIMARY};font-weight:600;">'
                    f"{speaker}</strong> {action}"
                )
            attribution_html = (
                f'<div style="margin-top:10px;padding:10px 14px;background:#F4F3F0;'
                f"border-radius:6px;font-size:13px;color:{TEXT_SECONDARY};"
                f'line-height:1.5;font-family:{FONT_STACK};">'
                f"{' '.join(parts)}</div>\n"
            )

        items_html += (
            f'<div style="padding:16px 0;{border_bottom}">\n'
            # Priority bar + title
            f'  <table cellpadding="0" cellspacing="0" border="0" '
            f'style="margin-bottom:6px;"><tr>'
            f'<td style="width:3px;height:16px;border-radius:2px;'
            f'background:{bar_color};"></td>'
            f'<td style="padding-left:8px;font-weight:600;font-size:15px;'
            f"letter-spacing:-0.2px;color:{TEXT_PRIMARY};"
            f'font-family:{FONT_STACK};">{title}</td>'
            f"</tr></table>\n"
            # Summary
            f'  <div style="font-size:14px;color:{TEXT_SECONDARY};line-height:1.6;'
            f'margin-top:6px;font-family:{FONT_STACK};">{summary}</div>\n'
            f"  {attribution_html}"
            f"</div>\n"
        )

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_SURFACE};border:1px solid {BORDER};border-radius:8px;'
        f'margin-bottom:20px;overflow:hidden;">\n'
        # Header
        f'<tr><td style="padding:16px 24px;border-bottom:1px solid {BORDER_LIGHT};'
        f'font-weight:600;font-size:14px;color:{TEXT_PRIMARY};font-family:{FONT_STACK};">\n'
        f'  <table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="width:26px;height:26px;background:#F4F3F0;border-radius:6px;'
        f'text-align:center;vertical-align:middle;font-size:13px;">&#9673;</td>'
        f'<td style="padding-left:10px;font-weight:600;font-size:14px;'
        f'color:{TEXT_PRIMARY};">Discussion Threads</td>'
        f"</tr></table>\n"
        f"</td></tr>\n"
        # Body
        f'<tr><td style="padding:0 24px;">\n{items_html}</td></tr>\n'
        f"</table>\n"
    )


def _render_participants(participants: list[dict] | None) -> str:
    if not participants:
        return ""
    tags_html = ""
    for p in participants:
        name = _e(p.get("name", ""))
        present = p.get("present", True)
        reason = p.get("absence_reason", "")

        if present:
            tags_html += (
                f'<td style="padding:0 4px 8px 0;"><div style="display:inline-block;'
                f"padding:5px 12px;background:#F4F3F0;border-radius:20px;font-size:13px;"
                f'color:#555;font-family:{FONT_STACK};">{name}</div></td>\n'
            )
        else:
            label = f"{name} ({_e(reason)})" if reason else name
            tags_html += (
                f'<td style="padding:0 4px 8px 0;"><div style="display:inline-block;'
                f"padding:5px 12px;background:#F4F3F0;border-radius:20px;font-size:13px;"
                f"color:#555;opacity:0.45;text-decoration:line-through;"
                f'font-family:{FONT_STACK};">{label}</div></td>\n'
            )

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_SURFACE};border:1px solid {BORDER};border-radius:8px;'
        f'margin-bottom:20px;overflow:hidden;">\n'
        # Header
        f'<tr><td style="padding:16px 24px;border-bottom:1px solid {BORDER_LIGHT};'
        f'font-weight:600;font-size:14px;color:{TEXT_PRIMARY};font-family:{FONT_STACK};">\n'
        f'  <table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="width:26px;height:26px;background:#F4F3F0;border-radius:6px;'
        f'text-align:center;vertical-align:middle;font-size:13px;">&#8857;</td>'
        f'<td style="padding-left:10px;font-weight:600;font-size:14px;'
        f'color:{TEXT_PRIMARY};">Participants</td>'
        f"</tr></table>\n"
        f"</td></tr>\n"
        # Body — wrapping row of tags
        f'<tr><td style="padding:16px 24px;">\n'
        f'  <table cellpadding="0" cellspacing="0" border="0"><tr>\n'
        f"  {tags_html}</tr></table>\n"
        f"</td></tr>\n"
        f"</table>\n"
    )


def _render_cta(
    call_url: str,
    edit_url: str | None = None,
    transcript_url: str | None = None,
) -> str:
    secondary_html = ""
    if edit_url or transcript_url:
        buttons = ""
        if edit_url:
            buttons += (
                f'<td style="padding:0 6px;"><a href="{_e(edit_url)}" '
                f'style="display:inline-block;background:{BRAND_ULTRA_LIGHT};'
                f"color:{BRAND};text-decoration:none;font-weight:500;font-size:14px;"
                f"padding:10px 24px;border-radius:6px;border:1px solid {BRAND_LIGHT};"
                f'font-family:{FONT_STACK};">Edit Summary</a></td>\n'
            )
        if transcript_url:
            buttons += (
                f'<td style="padding:0 6px;"><a href="{_e(transcript_url)}" '
                f'style="display:inline-block;background:{BRAND_ULTRA_LIGHT};'
                f"color:{BRAND};text-decoration:none;font-weight:500;font-size:14px;"
                f"padding:10px 24px;border-radius:6px;border:1px solid {BRAND_LIGHT};"
                f'font-family:{FONT_STACK};">View Transcript</a></td>\n'
            )
        secondary_html = (
            f'<table cellpadding="0" cellspacing="0" border="0" align="center" '
            f'style="margin-top:12px;"><tr>{buttons}</tr></table>\n'
        )

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="padding:36px 0 20px;">\n'
        f'<tr><td align="center">\n'
        f'  <a href="{_e(call_url)}" style="display:inline-block;background:{BRAND};'
        f"color:#ffffff;text-decoration:none;font-weight:600;font-size:16px;"
        f"padding:14px 40px;border-radius:8px;letter-spacing:-0.2px;"
        f'font-family:{FONT_STACK};">View Full Meeting &rarr;</a>\n'
        f"  {secondary_html}"
        f"</td></tr>\n"
        f"</table>\n"
    )


def _render_footer(
    unsubscribe_url: str | None = None,
    delivery_minutes: int | None = None,
    growth_mode: bool = False,
    settings_url: str | None = None,
    help_url: str = "https://app.meetmemoir.com/help",
) -> str:
    delivery_html = ""
    if delivery_minutes is not None:
        delivery_html = (
            f'<div style="font-family:{MONO_STACK};font-size:11px;'
            f'color:{TEXT_TERTIARY};margin-bottom:8px;">'
            f"Processed in {delivery_minutes} minutes</div>\n"
        )

    if growth_mode:
        links = []
        if unsubscribe_url:
            links.append(
                f'<a href="{_e(unsubscribe_url)}" '
                f'style="color:{BRAND};text-decoration:none;">Unsubscribe</a>'
            )
        links_html = (
            f' <span style="color:{BORDER};">&middot;</span> '.join(links)
        ) if links else ""
        links_div = f'  <div style="margin-top:4px;">{links_html}</div>\n' if links_html else ""
        return (
            f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
            f'style="padding:28px 0 48px;">\n'
            f'<tr><td align="center" style="font-size:12px;color:{TEXT_TERTIARY};'
            f'line-height:1.8;font-family:{FONT_STACK};">\n'
            f"  {delivery_html}"
            f'  <div>Meeting notes powered by <a href="https://app.meetmemoir.com" '
            f'style="color:{BRAND};text-decoration:none;">Memoir</a> &mdash; '
            f"AI meeting intelligence for teams that close deals.</div>\n"
            f"{links_div}"
            f"</td></tr>\n"
            f"</table>\n"
        )

    links = []
    if unsubscribe_url:
        links.append(
            f'<a href="{_e(unsubscribe_url)}" '
            f'style="color:{BRAND};text-decoration:none;">Unsubscribe</a>'
        )
    if settings_url:
        links.append(
            f'<a href="{_e(settings_url)}" '
            f'style="color:{BRAND};text-decoration:none;">Email Settings</a>'
        )
    links.append(
        f'<a href="{_e(help_url)}" '
        f'style="color:{BRAND};text-decoration:none;">Help Center</a>'
    )
    links_html = (
        f' <span style="color:{BORDER};">&middot;</span> '.join(links)
    )

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="padding:28px 0 48px;">\n'
        f'<tr><td align="center" style="font-size:12px;color:{TEXT_TERTIARY};'
        f'line-height:1.8;font-family:{FONT_STACK};">\n'
        f"  {delivery_html}"
        f'  <div>Powered by <a href="https://app.meetmemoir.com" '
        f'style="color:{BRAND};text-decoration:none;">Memoir</a></div>\n'
        f'  <div style="margin-top:4px;">{links_html}</div>\n'
        f"</td></tr>\n"
        f"</table>\n"
    )


# ── Public Functions ──────────────────────────────────────────────────────────

def build_recorder_email(
    meeting: dict,
    summary_data: dict,
    call_url: str,
    edit_url: str,
    transcript_url: str,
    unsubscribe_url: str,
    delivery_minutes: int | None = None,
) -> tuple[str, str]:
    """Build the full meeting summary email for the recorder.

    Returns:
        (subject, html_body) tuple.
    """
    title = meeting.get("title", "Untitled Meeting")
    subject = f"Your meeting summary: {title}"

    summary = summary_data.get("summary", {})
    meeting_meta = summary_data.get("meeting", {})

    body = "".join([
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'<tr><td style="padding:0 20px;">',
        _render_header(
            title=title,
            date_str=meeting.get("date"),
            duration_seconds=meeting.get("duration_seconds"),
            platform=meeting.get("platform"),
            participant_count=meeting.get("participant_count"),
            org_name=meeting.get("org_name"),
            recorder_name=meeting.get("recorded_by"),
        ),
        _render_tldr(summary.get("tldr")),
        _render_decisions(summary.get("decisions")),
        _render_open_questions(summary.get("open_questions")),
        _render_action_items(summary.get("action_items")),
        _render_discussion_threads(summary.get("discussion_threads")),
        _render_participants(meeting_meta.get("participants")),
        _render_cta(call_url, edit_url=edit_url, transcript_url=transcript_url),
        _render_footer(
            unsubscribe_url=unsubscribe_url,
            delivery_minutes=delivery_minutes,
            settings_url=_settings_url_from(call_url),
        ),
        "</td></tr></table>",
    ])

    return subject, _wrap_email(body)


def build_internal_email(
    meeting: dict,
    summary_data: dict,
    recipient_email: str,
    recipient_name: str,
    call_url: str,
    unsubscribe_url: str,
    delivery_minutes: int | None = None,
) -> tuple[str, str]:
    """Build a meeting recap email for an internal team member.

    Action items are shown with the recipient's items highlighted.

    Returns:
        (subject, html_body) tuple.
    """
    title = meeting.get("title", "Untitled Meeting")
    summary = summary_data.get("summary", {})

    action_items = summary.get("action_items", [])
    recipient_items = [
        it
        for it in action_items
        if (not recipient_email or not it.get("owner_email") or (it.get("owner_email") or "").lower() == recipient_email.lower())
    ]
    n = len(recipient_items)
    subject = f"Meeting recap: {title} — You have {n} action items"

    body = "".join([
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'<tr><td style="padding:0 20px;">',
        _render_header(
            title=title,
            date_str=meeting.get("date"),
            duration_seconds=meeting.get("duration_seconds"),
            platform=meeting.get("platform"),
            participant_count=meeting.get("participant_count"),
            org_name=meeting.get("org_name"),
            recorder_name=meeting.get("recorded_by"),
        ),
        _render_tldr(summary.get("tldr")),
        _render_action_items(
            action_items,
            filter_email=recipient_email,
            show_team_count=True,
        ),
        _render_decisions(summary.get("decisions")),
        _render_cta(call_url),
        _render_footer(
            unsubscribe_url=unsubscribe_url,
            delivery_minutes=delivery_minutes,
            settings_url=_settings_url_from(call_url),
        ),
        "</td></tr></table>",
    ])

    return subject, _wrap_email(body)


def build_external_email(
    meeting: dict,
    summary_data: dict,
    recipient_email: str,
    recipient_name: str,
    call_url: str,
    unsubscribe_url: str,
    delivery_minutes: int | None = None,
) -> tuple[str, str]:
    """Build a minimal meeting notes email for an external participant.

    Shows only the recipient's action items with minimal branding.

    Returns:
        (subject, html_body) tuple.
    """
    title = meeting.get("title", "Untitled Meeting")
    subject = f"Notes from your meeting: {title}"

    summary = summary_data.get("summary", {})
    action_items = summary.get("action_items", [])

    # Filter to only this recipient's items
    their_items = [
        it
        for it in action_items
        if (not recipient_email or not it.get("owner_email") or (it.get("owner_email") or "").lower() == recipient_email.lower())
    ]

    # Minimal header — title, date, recorder only
    date_formatted = _format_date(meeting.get("date"))
    recorder = _e(meeting.get("recorded_by", ""))

    meta_parts = [p for p in [date_formatted, recorder] if p]
    meta_text = " &middot; ".join(meta_parts)

    header_html = (
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{BG_SURFACE};border-bottom:1px solid {BORDER};">\n'
        f'<tr><td style="padding:32px 20px 28px 20px;">\n'
        f'  <h1 style="margin:0 0 8px 0;font-size:24px;font-weight:700;'
        f"letter-spacing:-0.5px;line-height:1.25;color:{TEXT_PRIMARY};"
        f'font-family:{FONT_STACK};">{_e(title)}</h1>\n'
        f'  <div style="font-size:13px;color:{TEXT_TERTIARY};'
        f'font-family:{MONO_STACK};">{meta_text}</div>\n'
        f"</td></tr>\n"
        f"</table>\n"
    )

    body = "".join([
        f'<table cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'<tr><td style="padding:0 20px;">',
        header_html,
        _render_tldr(summary.get("tldr")),
        _render_action_items(their_items),
        _render_cta(call_url),
        _render_footer(
            unsubscribe_url=unsubscribe_url,
            delivery_minutes=delivery_minutes,
            growth_mode=True,
        ),
        "</td></tr></table>",
    ])

    return subject, _wrap_email(body)
