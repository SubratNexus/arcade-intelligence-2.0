#!/usr/bin/env python3
"""Optional SMTP alerts with an explicit human-approval gate.

No email is sent unless all SMTP settings exist and a programme operator has
explicitly approved the event after review. A staff-like forum permission alone
is not a sufficient email-alert basis.
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
TRUSTED_AUTHOR_METHOD = "discourse_google_staff_group"


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


def _has_trusted_official_source(event: dict[str, Any]) -> bool:
    sources = event.get("sources")
    if not isinstance(sources, list):
        return False
    return any(
        isinstance(source, dict)
        and source.get("source_type") == "google_cloud_community"
        and source.get("source_domain") == "discuss.google.dev"
        and str(source.get("source_url") or "").startswith("https://discuss.google.dev/t/")
        and source.get("is_google_staff_author") is True
        and source.get("publisher_authority") == "google_staff_group"
        and source.get("author_verification_method") == TRUSTED_AUTHOR_METHOD
        for source in sources
    )


def _is_sendable_official_event(event: dict[str, Any]) -> bool:
    return (
        event.get("verification_status") == "officially_announced"
        and event.get("human_review_required") is False
        and event.get("notification_approved") is True
        and _has_trusted_official_source(event)
    )


def _format_body(event: dict[str, Any]) -> str:
    lines = [
        "ARCADE INTELLIGENCE — REVIEWED PROGRAMME ANNOUNCEMENT",
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
    lines.extend([
        "",
        "This alert was sent only after explicit human approval. Review the original "
        "source before changing a calculator or production website.",
    ])
    return "\n".join(lines)


def send_pending_alerts() -> dict[str, Any]:
    host = os.getenv("SMTP_HOST", "").strip()
    port_text = os.getenv("SMTP_PORT", "465").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = os.getenv("ALERT_EMAIL_TO", "").strip()
    sender = os.getenv("ALERT_EMAIL_FROM", "").strip() or username

    required = (host, port_text, username, password, recipient, sender)
    events_data = load_json(EVENTS_FILE, {"events": []})
    events = events_data.get("events", []) if isinstance(events_data, dict) else []
    official_candidates = [
        event for event in events
        if isinstance(event, dict) and event.get("verification_status") == "officially_announced"
    ]
    pending = [event for event in official_candidates if _is_sendable_official_event(event)]
    blocked_for_review = len(official_candidates) - len(pending)

    if not all(required):
        result = {
            "status": "disabled",
            "sent": 0,
            "pending_approved_announcements": len(pending),
            "official_candidates_waiting_for_review": blocked_for_review,
            "note": "Email is disabled because one or more SMTP settings are not configured.",
        }
        print(json.dumps(result, indent=2))
        return result

    try:
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("SMTP_PORT must be a valid TCP port number")

    state = load_json(STATE_FILE, {"schema_version": 1, "sent_fingerprints": {}})
    if not isinstance(state, dict):
        state = {"schema_version": 1, "sent_fingerprints": {}}
    sent_fingerprints = state.get("sent_fingerprints")
    if not isinstance(sent_fingerprints, dict):
        sent_fingerprints = {}

    sent = 0
    errors: list[str] = []
    for event in pending:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        source_urls = event.get("source_urls") if isinstance(event.get("source_urls"), list) else []
        points = event.get("explicit_point_mentions") if isinstance(event.get("explicit_point_mentions"), list) else []
        tiers = event.get("tier_mentions") if isinstance(event.get("tier_mentions"), list) else []
        fingerprints = event.get("content_fingerprints") if isinstance(event.get("content_fingerprints"), list) else []
        fingerprint = "|".join([
            str(event.get("title") or ""),
            str(event.get("verification_status") or ""),
            str(event.get("published_at") or ""),
            "|".join(str(value) for value in source_urls),
            "|".join(str(value) for value in fingerprints),
            "|".join(str(value) for value in points),
            "|".join(str(value) for value in tiers),
        ])
        if sent_fingerprints.get(event_id) == fingerprint:
            continue

        message = EmailMessage()
        message["Subject"] = _safe_header(
            f"Arcade Intelligence: {str(event.get('event_type', 'programme update')).replace('_', ' ').title()} — "
            f"{event.get('title', 'New reviewed announcement')}"
        )
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
            errors.append(type(exc).__name__)

    if sent:
        state["schema_version"] = 1
        state["updated_at"] = utc_now()
        state["sent_fingerprints"] = dict(list(sent_fingerprints.items())[-1000:])
        save_json(STATE_FILE, state)

    result = {
        "status": "success" if not errors else "partial",
        "sent": sent,
        "pending_approved_announcements": len(pending),
        "official_candidates_waiting_for_review": blocked_for_review,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    result = send_pending_alerts()
    raise SystemExit(1 if result.get("errors") else 0)
