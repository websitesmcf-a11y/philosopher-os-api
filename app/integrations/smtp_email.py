"""SMTP email — inbox connection checks and sending with app passwords.

Synchronous smtplib calls are wrapped in asyncio.to_thread by callers.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Well-known SMTP endpoints, keyed by email domain. Anything else falls
# back to smtp.<domain>:587 unless the user supplies an explicit host.
KNOWN_SMTP: dict[str, tuple[str, int]] = {
    "gmail.com": ("smtp.gmail.com", 587),
    "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "live.com": ("smtp.office365.com", 587),
    "msn.com": ("smtp.office365.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 587),
    "ymail.com": ("smtp.mail.yahoo.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
    "mac.com": ("smtp.mail.me.com", 587),
    "zoho.com": ("smtp.zoho.com", 587),
    "gmx.com": ("mail.gmx.com", 587),
    "gmx.net": ("mail.gmx.net", 587),
    "web.de": ("smtp.web.de", 587),
    "protonmail.com": ("smtp.protonmail.ch", 587),
    "proton.me": ("smtp.protonmail.ch", 587),
}


def resolve_smtp(email_address: str, smtp_host: str | None = None, smtp_port: str | int | None = None) -> tuple[str, int]:
    """SMTP host/port for an inbox: explicit override > known provider > smtp.<domain>."""
    if smtp_host:
        return smtp_host.strip(), int(smtp_port or 587)
    domain = email_address.rsplit("@", 1)[-1].lower().strip()
    if domain in KNOWN_SMTP:
        return KNOWN_SMTP[domain]
    return f"smtp.{domain}", int(smtp_port or 587)


def _connect(host: str, port: int) -> smtplib.SMTP:
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=20)
    conn = smtplib.SMTP(host, port, timeout=20)
    conn.ehlo()
    conn.starttls()
    conn.ehlo()
    return conn


def smtp_login_check(email_address: str, password: str, host: str, port: int, send_test: bool = True) -> str:
    """Log in via SMTP AUTH and (optionally) send a verification email to self.

    Returns a human-readable success detail; raises on any failure.
    """
    with _connect(host, port) as conn:
        conn.login(email_address, password)
        if send_test:
            msg = MIMEText(
                "This inbox is now connected to Socrates AI.\n\n"
                "This is the one-time verification send — no action needed."
            )
            msg["Subject"] = "Socrates AI — inbox connected"
            msg["From"] = email_address
            msg["To"] = email_address
            conn.send_message(msg)
    return f"SMTP login OK — verification email sent to {email_address}" if send_test else "SMTP login OK"


def smtp_send(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    to: list[str],
    subject: str,
    html: str | None = None,
    text: str | None = None,
    from_email: str | None = None,
    reply_to: str | None = None,
) -> None:
    """Send one email over SMTP AUTH. Raises on failure."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email or username
    msg["To"] = ", ".join(to)
    if reply_to:
        msg["Reply-To"] = reply_to
    if text:
        msg.attach(MIMEText(text, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))
    if not text and not html:
        msg.attach(MIMEText("", "plain"))

    with _connect(host, port) as conn:
        conn.login(username, password)
        conn.sendmail(msg["From"], to, msg.as_string())
