# briefings/email.py
"""
Email delivery for AI-generated weekly briefings.

send_briefing_email() reads EmailConfig for SMTP transport and
BriefingConfig for the recipient list. Each recipient receives an
individual copy so addresses remain private (no CC/BCC leakage).

Django's send_mass_mail() sends identical messages in one SMTP
connection, but exposes all recipients to the server. Using separate
EmailMultiAlternatives per recipient keeps it simple and private.
When the platform scales to hundreds of recipients, drop in Celery
and a task queue here — the interface stays the same.
"""

import logging

from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.smtp import EmailBackend

logger = logging.getLogger(__name__)


def send_briefing_email(briefing) -> int:
    """
    Deliver a completed WeeklyBriefing to all configured recipients.

    Reads transport settings from EmailConfig and the recipient list from
    BriefingConfig. Skips silently when delivery is disabled, no recipients
    are set, or SMTP is not configured. Email failures are logged per-recipient
    but never raise — a failed email must never mark the briefing as failed.

    Args:
        briefing: A WeeklyBriefing instance with status=ready.

    Returns:
        Number of emails successfully sent.
    """
    from briefings.models import BriefingConfig, EmailConfig

    config = BriefingConfig.get()
    email_cfg = EmailConfig.get()

    if not config.send_briefing_email:
        logger.debug("Briefing email delivery disabled in BriefingConfig — skipping.")
        return 0

    recipients = [r.strip() for r in config.briefing_email_recipients.split(",") if r.strip()]
    if not recipients:
        logger.warning("Briefing email enabled but no recipients configured — skipping.")
        return 0

    if not email_cfg.is_configured:
        logger.warning(
            "Briefing email enabled but EmailConfig.smtp_host is blank — skipping."
            " Configure SMTP in Briefing Settings."
        )
        return 0

    # Build a backend from DB config so .env credentials are not required.
    backend = EmailBackend(
        host=email_cfg.smtp_host,
        port=email_cfg.smtp_port,
        username=email_cfg.smtp_user,
        password=email_cfg.smtp_password,
        use_tls=email_cfg.use_tls,
        fail_silently=False,
    )

    subject = f"Weekly Intelligence Briefing — {briefing.period_end:%B %d, %Y}"
    # Plain-text body: markdown renders acceptably as plain text.
    text_body = (
        f"Weekly Intelligence Briefing\n"
        f"Period: {briefing.period_start:%Y-%m-%d} – {briefing.period_end:%Y-%m-%d}\n"
        f"Model: {briefing.model_used}\n\n"
        f"{briefing.content}"
    )

    sent = 0
    with backend:
        for recipient in recipients:
            try:
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text_body,
                    from_email=email_cfg.default_from_email,
                    to=[recipient],
                    connection=backend,
                )
                msg.send()
                sent += 1
                logger.info("Briefing email sent to %s (pk=%d).", recipient, briefing.pk)
            except Exception as exc:
                logger.exception(
                    "Failed to send briefing email to %s (pk=%d): %s",
                    recipient,
                    briefing.pk,
                    exc,
                )

    logger.info(
        "Briefing email delivery complete: %d/%d sent (pk=%d).",
        sent,
        len(recipients),
        briefing.pk,
    )
    return sent
