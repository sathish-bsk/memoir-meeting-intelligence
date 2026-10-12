"""
Email sending endpoint — POST /notifications/internal/send-email
"""

import logging
import uuid as _uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_session
from app.models.email import EmailLog, EmailTemplate

logger = logging.getLogger("notification")

router = APIRouter(prefix="/notifications/internal", tags=["email"])


# ─── Hardcoded fallback templates (used when DB is unavailable) ──

TEMPLATES: Dict[str, Dict[str, str]] = {
    "email_verification": {
        "subject": "Verify your email — Memoir",
        "html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Verify your email</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, please verify your email address by clicking the button below.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Verify email
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you didn't create an account, you can safely ignore this email.
  </p>
</div>
""",
    },
    "email_change_verification": {
        "subject": "Confirm your new email — Memoir",
        "html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Confirm your new email</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, you requested to change your email address. Click below to confirm.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Confirm new email
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you didn't request this change, you can safely ignore this email.
  </p>
</div>
""",
    },
    "password_reset": {
        "subject": "Reset your password — Memoir",
        "html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">Reset your password</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, click below to reset your password. This link expires in 1 hour.
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Reset password
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you didn't request a password reset, you can safely ignore this email.
  </p>
</div>
""",
    },
    "intelligence_digest": {
        "subject": "{title} — Memoir",
        "html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">{title}</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, your latest intelligence digest is ready.
  </p>
  <div style="background: #f8f7f4; border-radius: 8px; padding: 20px; margin: 16px 0;">
    <p style="color: #52525b; font-size: 14px; line-height: 1.6; white-space: pre-wrap;">{narrative}</p>
  </div>
  <p style="color: #a1a1aa; font-size: 13px;">Based on {total_calls} calls analyzed this period.</p>
  <a href="{link}" style="display: inline-block; background: #4F46E5; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    View full digest
  </a>
</div>
""",
    },
    "faq_digest": {
        "subject": "FAQ Digest — Memoir",
        "html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">FAQ Digest</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Hi {name}, here's a summary of the most frequently asked questions from your recent calls.
  </p>
  <div style="background: #f8f7f4; border-radius: 8px; padding: 20px; margin: 16px 0;">
    <p style="color: #52525b; font-size: 14px; line-height: 1.6; white-space: pre-wrap;">{narrative}</p>
  </div>
  <p style="color: #a1a1aa; font-size: 13px;">Based on {total_calls} calls analyzed this period.</p>
  <a href="{link}" style="display: inline-block; background: #4F46E5; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    View FAQ deep dive
  </a>
</div>
""",
    },
    "team_invite": {
        "subject": "You're invited to join {team_name} on Memoir",
        "html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
  <h2 style="color: #18181b; margin-bottom: 8px;">You're invited!</h2>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    {inviter_name} has invited you to join <strong>{team_name}</strong> on Memoir.
  </p>
  <p style="color: #52525b; font-size: 15px; line-height: 1.6;">
    Your invite code is: <strong style="font-size: 18px; letter-spacing: 1px;">{invite_code}</strong>
  </p>
  <a href="{link}" style="display: inline-block; background: #18181b; color: #fff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; margin: 16px 0;">
    Accept invite
  </a>
  <p style="color: #a1a1aa; font-size: 13px; margin-top: 24px;">
    If you don't have a Memoir account yet, you'll need to sign up first, then use the link above to join the team.
  </p>
</div>
""",
    },
}


# ─── Request schema ──────────────────────────────────────────────

class SendEmailRequest(BaseModel):
    to: EmailStr
    template: str
    data: Dict[str, Any] = {}
    trigger_type: str = "system"
    triggered_by: Optional[str] = None


# ─── Template loading ────────────────────────────────────────────

async def _load_template(template_name: str) -> Dict[str, str] | None:
    """Load a template from DB, falling back to hardcoded TEMPLATES dict."""
    session = await get_session()
    if session:
        try:
            async with session:
                result = await session.execute(
                    select(EmailTemplate).where(
                        EmailTemplate.name == template_name,
                        EmailTemplate.is_active == True,
                    )
                )
                tpl = result.scalar_one_or_none()
                if tpl:
                    return {"subject": tpl.subject, "html": tpl.html_body}
        except Exception as e:
            logger.warning(f"DB template lookup failed, using fallback: {e}")

    fallback = TEMPLATES.get(template_name)
    return fallback


# ─── Email logging ───────────────────────────────────────────────

async def _log_email(
    to_email: str,
    template_name: str,
    template_data: Dict[str, Any],
    trigger_type: str,
    triggered_by: str | None,
    status: str,
    via: str | None,
    error_message: str | None = None,
) -> None:
    """Insert a row into email_log. Best-effort — never raises."""
    session = await get_session()
    if not session:
        return
    try:
        async with session:
            log_entry = EmailLog(
                to_email=to_email,
                template_name=template_name,
                template_data=template_data,
                trigger_type=trigger_type,
                triggered_by=_uuid.UUID(triggered_by) if triggered_by else None,
                status=status,
                via=via,
                error_message=error_message,
            )
            session.add(log_entry)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to log email: {e}")


# ─── Endpoint ────────────────────────────────────────────────────

@router.post("/send-email")
async def send_email(
    body: SendEmailRequest,
    x_service_key: Optional[str] = Header(default=None, alias="X-Service-Key"),
):
    """Send a transactional email. Protected by internal service key."""
    if not x_service_key or x_service_key != settings.INTERNAL_SERVICE_KEY:
        raise HTTPException(403, "Invalid or missing service key")

    template = await _load_template(body.template)
    if not template:
        raise HTTPException(400, f"Unknown template: {body.template}")

    subject = template["subject"].format(**body.data)
    html = template["html"].format(**body.data)

    if not settings.can_send_email:
        logger.info(
            f"[DEV] Email to={body.to} subject=\"{subject}\"\n"
            f"  template={body.template} data={body.data}\n"
            f"  html_preview (first 200 chars): {html.strip()[:200]}"
        )
        await _log_email(
            to_email=body.to,
            template_name=body.template,
            template_data=body.data,
            trigger_type=body.trigger_type,
            triggered_by=body.triggered_by,
            status="skipped",
            via="console",
        )
        return {"status": "logged", "message": "Email logged to console (no email provider configured)"}

    # ─── SendGrid (priority) ────────────────────────────────
    if settings.SENDGRID_API_KEY:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=(settings.FROM_EMAIL, settings.FROM_NAME),
            to_emails=body.to,
            subject=subject,
            html_content=html,
        )

        # Add List-Unsubscribe headers if unsubscribe_url is present
        unsubscribe_url = body.data.get("unsubscribe_url")
        if unsubscribe_url:
            from sendgrid.helpers.mail import Header
            message.header = Header("List-Unsubscribe", f"<{unsubscribe_url}>")
            message.header = Header("List-Unsubscribe-Post", "List-Unsubscribe=One-Click")  # noqa: E501

        try:
            sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
            response = sg.send(message)
            logger.info(f"Email sent via SendGrid to={body.to} status={response.status_code}")
            await _log_email(
                to_email=body.to,
                template_name=body.template,
                template_data=body.data,
                trigger_type=body.trigger_type,
                triggered_by=body.triggered_by,
                status="sent",
                via="sendgrid",
            )
            return {"status": "sent", "via": "sendgrid", "status_code": response.status_code}
        except Exception as e:
            logger.error(f"SendGrid send failed to={body.to}: {e}")
            await _log_email(
                to_email=body.to,
                template_name=body.template,
                template_data=body.data,
                trigger_type=body.trigger_type,
                triggered_by=body.triggered_by,
                status="failed",
                via="sendgrid",
                error_message=str(e),
            )
            # Fall through to SMTP instead of raising
            logger.info("Falling back to SMTP after SendGrid failure")

    # ─── SMTP fallback (Gmail etc.) ──────────────────────────
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    sender_email = settings.SMTP_USER or settings.FROM_EMAIL
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.FROM_NAME} <{sender_email}>"
    msg["To"] = body.to

    # Add List-Unsubscribe headers if unsubscribe_url is present
    unsubscribe_url = body.data.get("unsubscribe_url")
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(sender_email, body.to, msg.as_string())
        logger.info(f"Email sent via SMTP to={body.to}")
        await _log_email(
            to_email=body.to,
            template_name=body.template,
            template_data=body.data,
            trigger_type=body.trigger_type,
            triggered_by=body.triggered_by,
            status="sent",
            via="smtp",
        )
        return {"status": "sent", "via": "smtp"}
    except Exception as e:
        logger.error(f"SMTP send failed to={body.to}: {e}")
        await _log_email(
            to_email=body.to,
            template_name=body.template,
            template_data=body.data,
            trigger_type=body.trigger_type,
            triggered_by=body.triggered_by,
            status="failed",
            via="smtp",
            error_message=str(e),
        )
        raise HTTPException(502, f"Failed to send email: {e}")
