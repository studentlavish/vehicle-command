"""Outgoing email (daily digest, alerts).

EMAIL_PROVIDER
    resend    Resend API directly — RESEND_API_KEY, EMAIL_FROM
    smtp      any SMTP server — SMTP_HOST, SMTP_PORT (587), SMTP_USER,
              SMTP_PASSWORD, SMTP_TLS (starttls | ssl | none), EMAIL_FROM
    emergent  legacy Emergent-managed proxy — EMERGENT_EMAIL_KEY
    (unset)   email disabled

Every message passes a safety gate first: no forms or credential requests
(G2); only absolute https links, no shorteners / IP literals / punycode /
userinfo, and link text must match the real host (G3).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("rdx.email")

EMERGENT_EMAIL_URL = "https://integrations.emergentagent.com/api/v1/email/send"
RESEND_URL = "https://api.resend.com/emails"

EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Vashu Hyundai")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip()
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")


def provider() -> str:
    """The active email provider ("" when email is not configured)."""
    p = os.environ.get("EMAIL_PROVIDER", "").strip().lower()
    if not p and EMAIL_KEY:
        return "emergent"  # older installs configured only EMERGENT_EMAIL_KEY
    return p if p in ("resend", "smtp", "emergent") else ""


_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = (
    "reply with your password", "reply with the code", "send your password", "cvv",
    "send us your password", "enter your password below", "confirm your card number",
    "your full card number", "seed phrase", "recovery phrase", "verify your card",
    "social security number", "confirm your bank details",
)
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan(); scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str, reply_to: str | None = None) -> str | None:
    """Send one transactional email. Returns the provider message id (or None).

    Raises RuntimeError when email is not configured and on hard failures.
    """
    p = provider()
    if not p:
        raise RuntimeError("Email is not configured (set EMAIL_PROVIDER)")
    _assert_safe_email(subject, html)
    reply = reply_to or EMAIL_REPLY_TO
    if p == "resend":
        return await _send_resend(to, subject, html, reply)
    if p == "smtp":
        return await asyncio.to_thread(_send_smtp, to, subject, html, reply)
    return await _send_emergent(to, subject, html, reply)


def _from_header() -> str:
    if not EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM is not set (the sender address, e.g. reports@yourdomain.com)")
    return formataddr((EMAIL_FROM_NAME, EMAIL_FROM))


async def _post_json(url: str, headers: dict, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


async def _send_resend(to: str, subject: str, html: str, reply: str | None) -> str | None:
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        raise RuntimeError("RESEND_API_KEY is not set")
    payload = {"from": _from_header(), "to": [to], "subject": subject, "html": html}
    if reply:
        payload["reply_to"] = reply
    data = await _post_json(RESEND_URL, {"Authorization": f"Bearer {key}"}, payload)
    return data.get("id")


def _send_smtp(to: str, subject: str, html: str, reply: str | None) -> str | None:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not set")
    port = int(os.environ.get("SMTP_PORT", "587"))
    mode = os.environ.get("SMTP_TLS", "starttls").strip().lower()
    msg = EmailMessage()
    msg["From"] = _from_header()
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=EMAIL_FROM.split("@")[-1] if "@" in EMAIL_FROM else None)
    if reply:
        msg["Reply-To"] = reply
    msg.set_content("This message needs an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")
    cls = smtplib.SMTP_SSL if mode == "ssl" else smtplib.SMTP
    with cls(host, port, timeout=30) as smtp:
        if mode == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        user = os.environ.get("SMTP_USER", "")
        if user:
            smtp.login(user, os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    return msg["Message-ID"]


async def _send_emergent(to: str, subject: str, html: str, reply: str | None) -> str | None:
    if not EMAIL_KEY:
        raise RuntimeError("EMERGENT_EMAIL_KEY is not configured")
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if reply:
        payload["contact_email"] = reply
    data = await _post_json(EMERGENT_EMAIL_URL, {"X-Email-Key": EMAIL_KEY}, payload)
    return data.get("id")
