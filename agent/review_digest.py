#!/usr/bin/env python3
"""Send one owner email digest at most once per 48 hours.

Every pending update receives its own Approve and Reject link. The links only
open a confirmation page; the future Netlify handler must require a second,
explicit POST confirmation before dispatching apply_review.py.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
QUEUE_FILE = DATA / "review-queue.json"
STATE_FILE = DATA / "review-delivery-state.json"
RESEND_ENDPOINT = "https://api.resend.com/emails"
DIGEST_INTERVAL = timedelta(hours=48)
DEFAULT_SENDER = "Arcade Tracker <onboarding@resend.dev>"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def review_signature(item_id: str, decision: str, issued_at: int, secret: str) -> str:
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    message = f"{item_id}.{decision}.{issued_at}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def review_url(base_url: str, item_id: str, decision: str, issued_at: int, secret: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("AGENT_REVIEW_BASE_URL must be an HTTPS URL")
    if parsed.path.rstrip("/").endswith("/api/agent-review"):
        endpoint = base_url.strip()
    else:
        endpoint = base_url.rstrip("/") + "/api/agent-review"
    query = urllib.parse.urlencode({
        "item_id": item_id,
        "action": decision,
        "ts": str(issued_at),
        "sig": review_signature(item_id, decision, issued_at, secret),
    })
    return endpoint + ("&" if "?" in endpoint else "?") + query


def _first_source(record: dict[str, Any], fallback: Any = None) -> str | None:
    for key in ("source_url", "badge_url"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    urls = record.get("source_urls")
    if isinstance(urls, list):
        return next((url for url in urls if isinstance(url, str) and url.startswith("https://")), None)
    if isinstance(fallback, str) and fallback.startswith("https://"):
        return fallback
    return None


def _card_html(item: dict[str, Any], base_url: str, issued_at: int, secret: str) -> str:
    item_id = str(item.get("item_id") or "")
    title = html.escape(str(item.get("title") or "Untitled update"))
    entity_type = html.escape(str(item.get("entity_type") or "update").replace("_", " ").title())
    change_type = html.escape(str(item.get("change_type") or "update").title())
    record = item.get("record") if isinstance(item.get("record"), dict) else {}
    verification = html.escape(str(record.get("verification_status") or record.get("status") or "Not verified"))
    warning = html.escape(str(item.get("warning") or "Review the source before approving."))
    evidence = html.escape(str(record.get("evidence") or record.get("verification_explanation") or "No evidence note supplied."))
    source_url = _first_source(record, item.get("source_url"))
    source = (
        f'<a href="{html.escape(source_url, quote=True)}">Open original source</a>'
        if source_url else "No source link supplied"
    )
    approve = html.escape(review_url(base_url, item_id, "approve", issued_at, secret), quote=True)
    reject = html.escape(review_url(base_url, item_id, "reject", issued_at, secret), quote=True)
    return f"""
    <section style="border:1px solid #d9deea;border-radius:14px;padding:16px;margin:0 0 14px;background:#ffffff;">
      <div style="font-size:12px;color:#526078;margin-bottom:7px;">{entity_type} · {change_type} · {verification}</div>
      <h3 style="font-size:17px;line-height:1.35;margin:0 0 9px;color:#172033;">{title}</h3>
      <p style="font-size:13px;line-height:1.5;color:#354158;margin:0 0 8px;"><strong>Evidence:</strong> {evidence}</p>
      <p style="font-size:13px;line-height:1.5;color:#7a4d13;margin:0 0 10px;">{warning}</p>
      <p style="font-size:13px;margin:0 0 14px;">{source}</p>
      <a href="{approve}" style="display:inline-block;background:#16794b;color:#fff;text-decoration:none;border-radius:8px;padding:10px 15px;margin:0 8px 6px 0;font-weight:600;">Approve this update</a>
      <a href="{reject}" style="display:inline-block;background:#a83242;color:#fff;text-decoration:none;border-radius:8px;padding:10px 15px;margin:0 0 6px;font-weight:600;">Reject this update</a>
      <div style="font-size:11px;color:#748095;margin-top:8px;">Review ID: {html.escape(item_id)}</div>
    </section>
    """


def build_email(queue: dict[str, Any], base_url: str, secret: str, issued_at: int) -> tuple[str, str, str]:
    pending = [item for item in queue.get("items", []) if isinstance(item, dict) and item.get("status") == "pending"]
    cards = "".join(_card_html(item, base_url, issued_at, secret) for item in pending)
    subject = f"Arcade Intelligence: {len(pending)} updates ready for your review"
    document = f"""
    <div style="max-width:720px;margin:0 auto;padding:20px;background:#f4f6fb;font-family:Arial,sans-serif;color:#172033;">
      <header style="padding:18px 4px 16px;">
        <div style="font-size:12px;letter-spacing:1px;color:#53617a;font-weight:700;">ARCADE INTELLIGENCE</div>
        <h1 style="font-size:24px;margin:8px 0;">Your 48-hour update review</h1>
        <p style="font-size:14px;line-height:1.6;color:#4d5a70;margin:0;">{len(pending)} individually reviewable update(s) were found. Each card has its own Approve and Reject action.</p>
      </header>
      <div style="background:#fff4df;border:1px solid #ead8ad;border-radius:10px;padding:12px 14px;margin-bottom:16px;font-size:13px;line-height:1.5;color:#674a17;">
        Nothing in this email is approved yet. Tapping a button opens a confirmation page; the update enters the approved database only after you confirm that specific item.
      </div>
      {cards}
      <footer style="font-size:12px;line-height:1.5;color:#718096;padding:8px 4px;">Unapproved discoveries must not be used by the app chatbot as verified knowledge. Check the original source, especially for programme dates, eligibility and point values.</footer>
    </div>
    """
    plain_lines = [subject, "", "Each update below has its own approval and rejection link.", "Nothing is approved until you confirm that individual item.", ""]
    for item in pending:
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        item_id = str(item.get("item_id") or "")
        plain_lines.extend([
            f"{item.get('title', 'Untitled update')} ({item.get('entity_type', 'update')} / {item.get('change_type', 'update')})",
            f"Review ID: {item_id}",
            f"Source: {_first_source(record, item.get('source_url')) or 'not supplied'}",
            f"Approve: {review_url(base_url, item_id, 'approve', issued_at, secret)}",
            f"Reject: {review_url(base_url, item_id, 'reject', issued_at, secret)}",
            "",
        ])
    return subject, document, "\n".join(plain_lines)


def send_digest(
    queue: dict[str, Any],
    state: dict[str, Any],
    environ: dict[str, str] | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    env = environ if environ is not None else os.environ
    current = now or utc_now()
    pending = [item for item in queue.get("items", []) if isinstance(item, dict) and item.get("status") == "pending"]
    if not pending:
        return {"status": "no_pending_updates", "pending": 0}, state

    required = (env.get("RESEND_API_KEY", "").strip(), env.get("OWNER_EMAIL", "").strip(),
                env.get("AGENT_REVIEW_SECRET", "").strip(), env.get("AGENT_REVIEW_BASE_URL", "").strip())
    if not all(required):
        return {
            "status": "disabled_missing_configuration",
            "pending": len(pending),
            "missing": [name for name, value in zip(("RESEND_API_KEY", "OWNER_EMAIL", "AGENT_REVIEW_SECRET", "AGENT_REVIEW_BASE_URL"), required) if not value],
        }, state

    last_sent = parse_time(state.get("last_sent_at"))
    if not force and last_sent and current - last_sent < DIGEST_INTERVAL:
        return {"status": "not_due", "pending": len(pending), "last_sent_at": state.get("last_sent_at")}, state

    issued_at = int(current.timestamp())
    base_url = env["AGENT_REVIEW_BASE_URL"].strip()
    secret = env["AGENT_REVIEW_SECRET"].strip()
    subject, document, plain = build_email(queue, base_url, secret, issued_at)
    sender = env.get("MAIL_FROM", "").strip() or DEFAULT_SENDER
    payload = json.dumps({
        "from": sender,
        "to": [env["OWNER_EMAIL"].strip()],
        "subject": subject,
        "html": document,
        "text": plain,
    }).encode("utf-8")
    request = urllib.request.Request(
        RESEND_ENDPOINT,
        data=payload,
        headers={"Authorization": f"Bearer {env['RESEND_API_KEY'].strip()}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Resend returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Resend email request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Could not connect to Resend") from exc

    new_state = dict(state)
    new_state.update({
        "schema_version": 1,
        "last_sent_at": iso_utc(current),
        "last_sent_item_count": len(pending),
        "last_sent_item_ids": [str(item.get("item_id")) for item in pending],
        "last_resend_response": response_body[:500],
    })
    return {"status": "sent", "pending": len(pending), "sent_at": iso_utc(current)}, new_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Send now even if fewer than 48 hours have elapsed.")
    args = parser.parse_args()
    queue = load_json(QUEUE_FILE, {"schema_version": 1, "items": []})
    state = load_json(STATE_FILE, {"schema_version": 1, "last_sent_at": None})
    result, new_state = send_digest(queue, state, force=args.force)
    if result.get("status") == "sent":
        save_json(STATE_FILE, new_state)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
