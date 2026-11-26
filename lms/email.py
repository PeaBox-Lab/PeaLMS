"""Optional SMTP delivery for the notification outbox.

The in-app inbox remains the source of truth. SMTP is a best-effort mirror: a
transport failure leaves the notification queued for bounded retries and then
dead-letters it without discarding the durable record.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import get_settings
from .db import row

logger = logging.getLogger("lms.email")


class DeliveryFailure(RuntimeError):
    """Base class for transport failures understood by the outbox."""


class PermanentDeliveryFailure(DeliveryFailure):
    """Retrying cannot help, for example when the recipient has no email."""


class TransientDeliveryFailure(DeliveryFailure):
    """Retrying after backoff may succeed."""


def smtp_transport(item: dict) -> dict:
    settings = get_settings()
    if not settings.smtp_enabled:
        raise PermanentDeliveryFailure("SMTP is not configured")
    employee = row(
        "SELECT display_name, work_email FROM employees WHERE employee_id=?",
        (item.get("recipient_employee_id"),),
    )
    recipient = (employee or {}).get("work_email")
    if not recipient:
        raise PermanentDeliveryFailure("Recipient has no work email")

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = str(item.get("subject") or "Learning notification")
    message.set_content(str(item.get("body") or ""))

    try:
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as client:
            if settings.smtp_starttls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise TransientDeliveryFailure(str(exc)) from exc
    return {"to": recipient, "template": item.get("template", "")}


__all__ = [
    "DeliveryFailure", "PermanentDeliveryFailure", "TransientDeliveryFailure",
    "smtp_transport",
]
