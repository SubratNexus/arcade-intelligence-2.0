#!/usr/bin/env python3
"""Apply one explicit owner decision to the approved knowledge database."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
APPROVED_FILE = DATA / "approved-knowledge.json"
QUEUE_FILE = DATA / "review-queue.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def apply_decision(item_id: str, decision: str, approved: dict[str, Any], queue: dict[str, Any], now: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    target = next((item for item in items if isinstance(item, dict) and item.get("item_id") == item_id), None)
    if target is None:
        raise KeyError("Review item was not found. It may have expired or been removed.")

    existing_status = target.get("status")
    if existing_status in {"approved", "rejected"}:
        if existing_status == ("approved" if decision == "approve" else "rejected"):
            return approved, queue  # Safe idempotent retry.
        raise ValueError("This review item already has a final decision.")
    if existing_status != "pending":
        raise ValueError("This review item is not pending and cannot be changed.")

    timestamp = now or utc_now()
    if decision == "approve":
        approved_items = approved.get("items") if isinstance(approved.get("items"), list) else []
        entity_id = target.get("entity_id")
        record = target.get("record")
        if not isinstance(record, dict) or not isinstance(entity_id, str):
            raise ValueError("Review item is missing its record or entity identity.")
        approved_items = [item for item in approved_items if not (isinstance(item, dict) and item.get("entity_id") == entity_id)]
        approved_items.append({
            "entity_type": target.get("entity_type"),
            "entity_id": entity_id,
            "title": target.get("title"),
            "record": record,
            "digest": target.get("digest"),
            "source_url": target.get("source_url"),
            "approved_at": timestamp,
            "approved_by": "owner_email_review",
            "approval_method": "explicit_per_item_confirmation",
        })
        approved["items"] = approved_items
        approved["updated_at"] = timestamp
        approved["knowledge_version"] = hashlib.sha256(json.dumps(
            [{"entity_id": x.get("entity_id"), "digest": x.get("digest")} for x in approved_items],
            ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()[:16]
        target["status"] = "approved"
    else:
        target["status"] = "rejected"
    target["review_decision"] = decision
    target["reviewed_at"] = timestamp
    target["review_note"] = "Owner decision received through the per-item review flow."
    queue["last_reviewed_at"] = timestamp
    return approved, queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--decision", required=True, choices=("approve", "reject"))
    args = parser.parse_args()
    approved = load_json(APPROVED_FILE, {"schema_version": 1, "items": []})
    queue = load_json(QUEUE_FILE, {"schema_version": 1, "items": []})
    new_approved, new_queue = apply_decision(args.item_id, args.decision, approved, queue)
    save_json(APPROVED_FILE, new_approved)
    save_json(QUEUE_FILE, new_queue)
    print(json.dumps({"status": "success", "item_id": args.item_id, "decision": args.decision}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
