"""Sends the report over SMTP. Configure with environment variables (GitHub secrets):

  SMTP_USER      the sending Gmail address
  SMTP_PASSWORD  a Gmail *app password* (Google Account > Security > App passwords), not your login
  EMAIL_TO       where to send the report (defaults to SMTP_USER)
  SMTP_HOST / SMTP_PORT  optional, default smtp.gmail.com:465
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)


def send_report(subject: str, html_body: str, text_body: str) -> bool:
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASSWORD")
    if not (user and pw):
        log.warning("SMTP_USER / SMTP_PASSWORD not set; not emailing (report saved to disk)")
        return False
    to = os.environ.get("EMAIL_TO") or user
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)
    log.info("Emailed report to %s", to)
    return True
