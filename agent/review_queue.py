#!/usr/bin/env python3
"""Build a human-review queue and a separate approved-only knowledge database.

The existing public-feed.json remains the agent's discovery/observation feed.
This module copies the current feed into an approved baseline once, then turns
future additions and material edits into individually reviewable proposals.
Nothing enters approved-knowledge.json after bootstrap unless an explicit
approve decision is applied by apply_review.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
FEED_FILE = DATA / "public-feed.json"
APPROVED_FILE = DATA / "approved-knowledge.json"
QUEUE_FILE = DATA / "review-queue.json"

ENTITY_GROUPS = (
    ("programme_event", "programme_events"),
    ("skill_badge", "skill_badges"),
    ("arcade_game", "arcade_games"),
    ("completion_badge", "completion_badges"),
    ("youtube_video", "youtube_videos"),
    ("unresolved_candidate", "unresolved_candidates"),
)
TRANSIENT_FIELDS = {
    "last_seen", "last_observed_at", "evidence_score", "source_count",
}


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


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in TRANSIENT_FIELDS}


def _source_url(record: dict[str, Any]) -> str | None:
    for key in ("source_url", "badge_url"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    urls = record.get("source_urls")
    if isinstance(urls, list):
        for value in urls:
            if isinstance(value, str) and value.startswith("https://"):
                return value
    sources = record.get("sources")
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict):
                value = source.get("source_url")
                if isinstance(value, str) and value.startswith("https://"):
                    return value
    return None


def _entity_identity(entity_type: str, record: dict[str, Any]) -> str:
    preferred = record.get("event_id") or record.get("video_id")
    if isinstance(preferred, str) and preferred.strip():
        return f"{entity_type}:{preferred.strip()}"
    # For catalogue items, title + URL avoids collisions where several badges
    # share the same Google Skills landing page.
    stable = "|".join((
        entity_type,
        str(record.get("title") or "").strip().casefold(),
        str(_source_url(record) or "").strip().casefold(),
    ))
    return f"{entity_type}:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]}"


def extract_entities(feed: Any) -> list[dict[str, Any]]:
    if not isinstance(feed, dict):
        return []
    entities: list[dict[str, Any]] = []
    for entity_type, field in ENTITY_GROUPS:
        values = feed.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            title = re.sub(r"\s+", " ", str(value.get("title") or "")).strip()[:240]
            if not title:
                continue
            record = dict(value)
            entity_id = _entity_identity(entity_type, record)
            stable = _stable_record(record)
            digest = _json_digest(stable)
            entities.append({
                "entity_type": entity_type,
                "entity_id": entity_id,
                "title": title,
                "record": record,
                "digest": digest,
                "source_url": _source_url(record),
            })
    return entities


def empty_approved_database() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "database_name": "Arcade Intelligence approved knowledge",
        "initialization_status": "not_initialized",
        "initialized_at": None,
        "updated_at": None,
        "knowledge_version": _json_digest([])[:16],
        "items": [],
        "policy": (
            "Items in this file are the app-safe knowledge layer. Existing records present "
            "at the first bootstrap are grandfathered as the starting baseline. Future "
            "discoveries and material edits require an explicit owner approval."
        ),
    }


def empty_review_queue() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": None,
        "items": [],
        "policy": "Pending items are proposals only and must not be used as approved app knowledge.",
    }


def build_review_queue(
    feed: Any,
    approved: Any,
    existing_queue: Any,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (approved database, review queue); does not write files."""
    now = generated_at or utc_now()
    approved_db = dict(approved) if isinstance(approved, dict) else empty_approved_database()
    if not isinstance(approved_db.get("items"), list):
        approved_db["items"] = []
    queue = dict(existing_queue) if isinstance(existing_queue, dict) else empty_review_queue()
    if not isinstance(queue.get("items"), list):
        queue["items"] = []

    entities = extract_entities(feed)
    existing_approved = {
        item.get("entity_id"): item
        for item in approved_db["items"]
        if isinstance(item, dict) and isinstance(item.get("entity_id"), str)
    }

    # A one-time baseline prevents the first setup run from emailing the owner
    # hundreds of records that were already present before this review feature.
    if approved_db.get("initialization_status") != "initialized":
        baseline_items = []
        for entity in entities:
            # Only established catalogue classifications form the initial
            # baseline. Programme announcements and video observations still
            # require an owner decision unless the legacy watcher already
            # recorded an explicit programme approval.
            baseline_eligible = entity["entity_type"] in {
                "skill_badge", "arcade_game", "completion_badge"
            }
            record = entity["record"]
            previously_approved_event = (
                entity["entity_type"] == "programme_event"
                and record.get("notification_approved") is True
                and record.get("review_decision") == "approved"
            )
            if not (baseline_eligible or previously_approved_event):
                continue
            baseline_items.append({
                **entity,
                "approved_at": now,
                "approved_by": "initial_existing_repository_baseline",
                "approval_method": "grandfathered_initial_baseline",
            })
        approved_db["items"] = baseline_items
        approved_db["initialization_status"] = "initialized"
        approved_db["initialized_at"] = now
        approved_db["updated_at"] = now
        existing_approved = {item["entity_id"]: item for item in baseline_items}

    queue_by_id = {
        item.get("item_id"): item
        for item in queue["items"]
        if isinstance(item, dict) and isinstance(item.get("item_id"), str)
    }
    for entity in entities:
        approved_item = existing_approved.get(entity["entity_id"])
        if approved_item and approved_item.get("digest") == entity["digest"]:
            continue
        change_type = "add" if approved_item is None else "update"
        item_id = _json_digest({
            "entity_id": entity["entity_id"],
            "change_type": change_type,
            "digest": entity["digest"],
        })[:24]
        if item_id in queue_by_id:
            continue
        item = {
            "item_id": item_id,
            "entity_type": entity["entity_type"],
            "entity_id": entity["entity_id"],
            "change_type": change_type,
            "title": entity["title"],
            "source_url": entity["source_url"],
            "record": entity["record"],
            "digest": entity["digest"],
            "status": "pending",
            "detected_at": now,
            "reviewed_at": None,
            "review_decision": None,
            "review_note": None,
            "warning": (
                "Unresolved discovery candidate: inspect the source and classification carefully."
                if entity["entity_type"] == "unresolved_candidate"
                else "Programme announcement: verify the official source, current dates, eligibility and point wording before approving."
                if entity["entity_type"] == "programme_event"
                else "Video observation: treat this as a secondary lead, not official confirmation."
                if entity["entity_type"] == "youtube_video"
                else "Discovery evidence is not the same as proof of current availability or point value."
            ),
        }
        queue["items"].append(item)
        queue_by_id[item_id] = item

    queue["generated_at"] = now
    approved_db["knowledge_version"] = _json_digest([
        {"entity_id": item.get("entity_id"), "digest": item.get("digest")}
        for item in approved_db["items"] if isinstance(item, dict)
    ])[:16]
    return approved_db, queue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-if-needed", action="store_true", help="Seed the initial baseline once.")
    args = parser.parse_args()
    feed = load_json(FEED_FILE, {})
    approved = load_json(APPROVED_FILE, empty_approved_database())
    queue = load_json(QUEUE_FILE, empty_review_queue())
    new_approved, new_queue = build_review_queue(feed, approved, queue)
    save_json(APPROVED_FILE, new_approved)
    save_json(QUEUE_FILE, new_queue)
    pending = sum(1 for item in new_queue["items"] if item.get("status") == "pending")
    print(json.dumps({
        "status": "success",
        "baseline_initialized": new_approved.get("initialization_status") == "initialized",
        "approved_items": len(new_approved["items"]),
        "pending_reviews": pending,
        "total_review_records": len(new_queue["items"]),
        "knowledge_version": new_approved.get("knowledge_version"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
