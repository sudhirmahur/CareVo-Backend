"""E-mail templates + outbox. Sends through SMTP when configured; otherwise only records the message."""
import re
import smtplib
import threading
from email.message import EmailMessage
from html import escape
from typing import Optional

from app import config
from app.database import email_outbox_collection, email_templates_collection, users_collection
from app.utils import now

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

DEFAULT_TEMPLATES = [
    ("welcome", "Welcome Email", "Welcome to CareVo, {{name}}!", "Hi {{name}},\n\nWelcome to CareVo. Your account is ready: {{app_url}}\n\nGood luck!"),
    ("verification", "Account Verification", "Verify your CareVo account", "Hi {{name}},\n\nUse this code to verify your account: {{code}}\nIt expires in 10 minutes."),
    ("password_reset", "Password Reset", "Reset your CareVo password", "Hi {{name}},\n\nUse this link to choose a new password: {{link}}\nIf you did not ask for this, ignore this e-mail."),
    ("otp", "Two-factor code", "Your CareVo sign-in code", "Hi {{name}},\n\nYour one-time code is {{code}}. It expires in 10 minutes. Never share it with anyone."),
    ("application_status", "Application Status Update", "Update on your application for {{job_title}}", "Hi {{name}},\n\nYour application for {{job_title}} at {{company}} is now: {{status}}."),
    ("interview_invite", "Interview Invitation", "Interview scheduled: {{job_title}}", "Hi {{name}},\n\n{{company}} scheduled an interview for {{job_title}} on {{when}}.\n{{details}}"),
    ("recruiter_verification", "Recruiter Verification", "Your company on CareVo: {{status}}", "Hi {{name}},\n\nYour company {{company}} was {{status}}. {{details}}"),
    ("subscription", "Subscription Confirmation", "Your CareVo Premium is active", "Hi {{name}},\n\nThank you! Your {{plan}} plan is active until {{until}}.\nAmount paid: {{amount}}."),
    ("job_alert", "Job Alert", "{{status}} new job(s) match your alerts", "Hi {{name}},\n\nNew jobs for you: {{details}}\nOpen CareVo to see them: {{app_url}}/jobs"),
    ("referral_invite", "Referral Invitation", "{{name}} invited you to CareVo", "Hi,\n\n{{name}} thinks CareVo can help your career. Join here: {{link}}"),
    ("offer_letter", "Offer Letter Notification", "You have a job offer from {{company}}", "Hi {{name}},\n\n{{company}} sent you an offer for {{designation}}. Open CareVo to review and respond: {{link}}"),
    ("team_invite", "Team Invitation", "{{company}} invited you to join their hiring team", "Hi {{name}},\n\n{{company}} added you to their CareVo hiring team as {{role}}."),
    ("recruiter_message", "Recruiter Message", "{{subject}}", "Hi {{name}},\n\n{{message}}\n\n- {{company}}"),
]


def seed_templates() -> None:
    for key, name, subject, body in DEFAULT_TEMPLATES:
        if not email_templates_collection.find_one({"key": key}):
            ts = now()
            email_templates_collection.insert_one({"key": key, "name": name, "subject": subject, "body": body, "enabled": True, "created_at": ts, "updated_at": ts})


def render(text: str, ctx: dict) -> str:
    return PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), "")), text or "")


def placeholders(text: str) -> list[str]:
    return sorted(set(PLACEHOLDER.findall(text or "")))


def _smtp_send(to: str, subject: str, body: str, outbox_id) -> None:
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
        msg.set_content(body)
        msg.add_alternative("<pre style='font-family:inherit;white-space:pre-wrap'>" + escape(body) + "</pre>", subtype="html")
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
            if config.SMTP_TLS:
                s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        email_outbox_collection.update_one({"_id": outbox_id}, {"$set": {"status": "sent", "sent_at": now()}})
    except Exception as exc:  # pragma: no cover - needs a real SMTP server
        email_outbox_collection.update_one({"_id": outbox_id}, {"$set": {"status": "failed", "error": str(exc)[:200]}})


def send_template(key: str, to: str, ctx: dict, user_id=None, force: bool = False) -> Optional[dict]:
    """Render the template and send/record it. Returns the outbox row (None when the template is disabled)."""
    tpl = email_templates_collection.find_one({"key": key})
    if not tpl or (not tpl.get("enabled", True) and not force):
        return None
    if user_id is not None and not force:
        u = users_collection.find_one({"_id": user_id}, {"notification_prefs": 1, "privacy": 1})
        prefs = ((u or {}).get("notification_prefs") or {}).get("email_enabled")
        marketing_only = key in ("promo",)
        if prefs is False and key not in ("otp", "password_reset", "verification"):
            return None
        if marketing_only and ((u or {}).get("privacy") or {}).get("marketing_emails") is False:
            return None
    full = {"app_url": config.PUBLIC_APP_URL, **ctx}
    row = {"to": to, "template": key, "subject": render(tpl["subject"], full)[:200], "body": render(tpl["body"], full), "status": "queued" if config.SMTP_HOST else "recorded", "user_id": user_id, "created_at": now()}
    row["_id"] = email_outbox_collection.insert_one(dict(row)).inserted_id
    if config.SMTP_HOST:
        threading.Thread(target=_smtp_send, args=(to, row["subject"], row["body"], row["_id"]), daemon=True).start()
    return row
