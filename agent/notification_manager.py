#!/usr/bin/env python3
"""Optional SMTP email alerts for newly detected Google-staff announcements.

No email is sent unless all SMTP environment variables are configured as
GitHub Actions secrets. Credentials are never written to logs or data files.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
EVENTS_FILE = DATA / "program-events.json"
STATE_FILE = DATA / "notification-state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _safe_header(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("Email header contains a newline")
    return value


def _format_body(event: dict[str, Any]) -> str:
    lines = [
        "ARCADE INTELLIGENCE — OFFICIAL PROGRAMME ANNOUNCEMENT",
        "",
        f"Title: {event.get('title', 'Untitled event')}",
        f"Type: {event.get('event_type', 'unknown')}",
        f"Verification: {event.get('verification_status', 'unknown')}",
        f"Published: {event.get('published_at') or 'Not available'}",
        f"Evidence note: {event.get('verification_explanation', '')}",
        "",
        "Explicit point references (quoted from sources; not calculated):",
    ]
    points = event.get("explicit_point_mentions") or []
    lines.extend([f"- {item}" for item in points] if points else ["- None extracted"])
    lines.extend(["", "Tier mentions (not an eligibility decision):"])
    tiers = event.get("tier_mentions") or []
    lines.extend([f"- {item}" for item in tiers] if tiers else ["- None extracted"])
    lines.extend(["", "Original sources:"])
    lines.extend([f"- {url}" for url in event.get("source_urls", [])])
    lines.extend(["", "Important: This is an evidence-linked alert. Review the original announcement before changing your calculator or production website."])
    return "\n".join(lines)


def send_pending_alerts() -> dict[str, Any]:
    host = os.getenv("SMTP_HOST", "").strip()
    port_text = os.getenv("SMTP_PORT", "465").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = os.getenv("ALERT_EMAIL_TO", "").strip()
    sender = os.getenv("ALERT_EMAIL_FROM", "").strip() or username

    required = (host, port_text, username, password, recipient, sender)
    if not all(required):
        result = {"status": "disabled", "sent": 0, "pending_official_announcements": 0, "note": "Email is safely disabled because one or more SMTP settings are not configured."}
        print(json.dumps(result, indent=2))
        return result
    try:
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("SMTP_PORT must be a valid TCP port number")

    events_data = load_json(EVENTS_FILE, {"events": []})
    state = load_json(STATE_FILE, {"schema_version": 1, "sent_fingerprints": {}})
    sent_fingerprints = state.get("sent_fingerprints") if isinstance(state.get("sent_fingerprints"), dict) else {}
    pending = [event for event in events_data.get("events", []) if event.get("verification_status") == "officially_announced"]
    sent = 0
    errors: list[str] = []

    for event in pending:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        fingerprint = "|".join([
            str(event.get("title") or ""),
            str(event.get("verification_status") or ""),
            str(event.get("published_at") or ""),
            "|".join(event.get("source_urls") or []),
            "|".join(event.get("content_fingerprints") or []),
            "|".join(event.get("explicit_point_mentions") or []),
            "|".join(event.get("tier_mentions") or []),
        ])
        if sent_fingerprints.get(event_id) == fingerprint:
            continue
        message = EmailMessage()
        message["Subject"] = _safe_header(f"Arcade Intelligence: {event.get('event_type', 'programme update').replace('_', ' ').title()} — {event.get('title', 'New official announcement')}")
        message["From"] = _safe_header(sender)
        message["To"] = _safe_header(recipient)
        message.set_content(_format_body(event))
        try:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as smtp:
                    smtp.login(username, password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=20) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    smtp.login(username, password)
                    smtp.send_message(message)
            sent_fingerprints[event_id] = fingerprint
            sent += 1
        except Exception as exc:
            # Do not print SMTP host details, credentials, or message contents.
            errors.append(type(exc).__name__)

    if sent:
        state["schema_version"] = 1
        state["updated_at"] = utc_now()
        state["sent_fingerprints"] = dict(list(sent_fingerprints.items())[-1000:])
        save_json(STATE_FILE, state)
    result = {"status": "success" if not errors else "partial", "sent": sent, "pending_official_announcements": len(pending), "errors": errors}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    result = send_pending_alerts()
    raise SystemExit(1 if result.get("errors") else 0)
