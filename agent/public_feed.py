#!/usr/bin/env python3
"""Build a safe, read-only knowledge feed for Arcade Journey Tracker.

The feed combines public programme observations with the separately discovered
Google Skills catalogue. It preserves schema_version 1 for existing consumers
and adds versioning, catalogue sections and a bounded change history.

This module never invents badge point values or claims current availability.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
EVENTS_FILE = DATA / "program-events.json"
YOUTUBE_FILE = DATA / "youtube-observations.json"
CATALOGUE_FILE = DATA / "catalogue.json"
OUTPUT_FILE = DATA / "public-feed.json"

ALLOWED_EVENT_HOSTS = {"discuss.google.dev", "youtube.com", "www.youtube.com"}
ALLOWED_CATALOGUE_HOSTS = {
    "skills.google", "www.skills.google", "cloudskillsboost.google", "www.cloudskillsboost.google"
}
ALLOWED_IMAGE_HOSTS = {"cdn.qwiklabs.com", "www.gstatic.com", "storage.googleapis.com"}
GENERIC_TITLES = {
    "google skills", "choose your learning path", "cloud", "google cloud",
    "badge", "skill badge", "arcade", "image", "icon", "logo"
}
MAX_HISTORY = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def safe_url(value: Any, allowed_hosts: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
            or parsed.username or parsed.password or port):
        return None
    return value


def _clean_text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _catalogue_entry(entry: Any, category: str) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or entry.get("verified") is not True:
        return None
    title = _clean_text(entry.get("title"), 180)
    if len(title) < 5 or title.casefold() in GENERIC_TITLES:
        return None
    source_url = safe_url(entry.get("source_url"), ALLOWED_CATALOGUE_HOSTS)
    badge_url = safe_url(entry.get("badge_url"), ALLOWED_CATALOGUE_HOSTS) or source_url
    image_url = safe_url(entry.get("image_url"), ALLOWED_IMAGE_HOSTS)
    if not source_url and not badge_url:
        return None
    aliases = entry.get("aliases", [])
    return {
        "title": title,
        "aliases": [_clean_text(x, 100) for x in aliases[:12] if isinstance(x, str)] if isinstance(aliases, list) else [],
        "category": category,
        "status": _clean_text(entry.get("status") or "unconfirmed", 40),
        "verified": True,
        "evidence": _clean_text(entry.get("evidence"), 300),
        "source_url": source_url or badge_url,
        "badge_url": badge_url,
        "image_url": image_url,
        "first_seen": _clean_text(entry.get("first_seen"), 40) or None,
        "last_seen": _clean_text(entry.get("last_seen"), 40) or None,
        "point_value": None,
        "availability_confirmed": False,
    }


def _candidate(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    title = _clean_text(entry.get("title") or "Unresolved Google Skills candidate", 180)
    source_url = safe_url(entry.get("source_url"), ALLOWED_CATALOGUE_HOSTS)
    badge_url = safe_url(entry.get("badge_url"), ALLOWED_CATALOGUE_HOSTS) or source_url
    image_url = safe_url(entry.get("image_url"), ALLOWED_IMAGE_HOSTS)
    if not source_url and not badge_url:
        return None
    return {
        "title": title,
        "category": _clean_text(entry.get("category") or "unknown", 40),
        "confidence": entry.get("confidence") if isinstance(entry.get("confidence"), (int, float)) and math.isfinite(entry.get("confidence")) else None,
        "evidence": _clean_text(entry.get("evidence"), 300),
        "source_url": source_url or badge_url,
        "badge_url": badge_url,
        "image_url": image_url,
        "review_status": "pending_review",
        "last_seen": _clean_text(entry.get("last_seen"), 40) or None,
    }


def _event_records(events_data: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    raw_events = events_data.get("events", []) if isinstance(events_data, dict) else []
    for event in raw_events if isinstance(raw_events, list) else []:
        if not isinstance(event, dict) or not _clean_text(event.get("title"), 300):
            continue
        source_urls = [safe for safe in (
            safe_url(url, ALLOWED_EVENT_HOSTS)
            for url in (event.get("source_urls") if isinstance(event.get("source_urls"), list) else [])
        ) if safe]
        sources: list[dict[str, Any]] = []
        for source in event.get("sources", []) if isinstance(event.get("sources"), list) else []:
            if not isinstance(source, dict):
                continue
            safe_source = safe_url(source.get("source_url"), ALLOWED_EVENT_HOSTS)
            if not safe_source:
                continue
            sources.append({
                "source_type": _clean_text(source.get("source_type") or "unknown", 60),
                "source_name": _clean_text(source.get("source_name") or "Public source", 120),
                "source_domain": _clean_text(source.get("source_domain"), 120),
                "source_url": safe_source,
                "published_at": source.get("published_at"),
                "publisher_authority": _clean_text(source.get("publisher_authority") or "unknown", 80),
                "is_google_staff_author": source.get("is_google_staff_author") is True,
                "author_verification_method": _clean_text(source.get("author_verification_method") or "unknown", 80),
            })
        events.append({
            "event_id": _clean_text(event.get("event_id"), 80),
            "event_type": _clean_text(event.get("event_type") or "unclassified", 80),
            "title": _clean_text(event.get("title"), 300),
            "verification_status": _clean_text(event.get("verification_status") or "unverified", 80),
            "evidence_score": event.get("evidence_score") if isinstance(event.get("evidence_score"), (int, float)) and math.isfinite(event.get("evidence_score")) else None,
            "verification_explanation": _clean_text(event.get("verification_explanation"), 1000),
            "published_at": event.get("published_at"),
            "last_observed_at": event.get("last_observed_at"),
            "source_count": len(set(source_urls)),
            "source_urls": list(dict.fromkeys(source_urls))[:10],
            "sources": sources[:10],
            "explicit_point_mentions": [_clean_text(x, 200) for x in event.get("explicit_point_mentions", [])[:20]] if isinstance(event.get("explicit_point_mentions"), list) else [],
            "tier_mentions": [_clean_text(x, 80) for x in event.get("tier_mentions", [])[:20]] if isinstance(event.get("tier_mentions"), list) else [],
            "human_review_required": event.get("human_review_required") is not False,
            "notification_approved": event.get("notification_approved") is True,
            "review_decision": _clean_text(event.get("review_decision") or "pending", 30),
        })
    events.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return events[:300]


def _video_records(youtube_data: Any) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    raw_videos = youtube_data.get("observations", []) if isinstance(youtube_data, dict) else []
    for video in raw_videos if isinstance(raw_videos, list) else []:
        if not isinstance(video, dict) or not _clean_text(video.get("title"), 300):
            continue
        source_url = safe_url(video.get("source_url"), {"youtube.com", "www.youtube.com"})
        if not source_url:
            continue
        thumbnail = safe_url(video.get("thumbnail_url"), {"i.ytimg.com", "img.youtube.com"})
        videos.append({
            "title": _clean_text(video.get("title"), 300),
            "source_name": _clean_text(video.get("source_name") or "YouTube channel", 120),
            "source_url": source_url,
            "published_at": video.get("published_at"),
            "publisher_type": _clean_text(video.get("publisher_type") or "unknown", 80),
            "event_type": _clean_text(video.get("event_type") or "unclassified", 80),
            "matched_keywords": [_clean_text(x, 100) for x in video.get("matched_keywords", [])[:20]] if isinstance(video.get("matched_keywords"), list) else [],
            "thumbnail_url": thumbnail,
        })
    videos.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return videos[:100]


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identity(item: dict[str, Any], prefix: str) -> str:
    stable = item.get("event_id") or item.get("title") or item.get("source_url") or "unknown"
    return prefix + ":" + _clean_text(stable, 180).casefold()


def _stable_item(item: dict[str, Any], entity_type: str) -> dict[str, Any]:
    """Remove observation timestamps before comparing substantive knowledge."""
    cleaned = dict(item)
    cleaned.pop("last_seen", None)
    cleaned.pop("last_observed_at", None)
    if entity_type == "programme_event":
        cleaned.pop("evidence_score", None)
    return cleaned


def _material_projection(feed: dict[str, Any]) -> dict[str, Any]:
    """Exclude polling timestamps so unchanged observations do not create fake updates."""
    event_fields = ("event_id", "event_type", "title", "verification_status", "published_at",
                    "source_urls", "verification_explanation", "explicit_point_mentions", "tier_mentions",
                    "human_review_required", "notification_approved", "review_decision")
    return {
        "programme_events": [{k: e.get(k) for k in event_fields} for e in feed.get("programme_events", [])],
        "skill_badges": [_stable_item(x, "skill_badge") for x in feed.get("skill_badges", [])],
        "arcade_games": [_stable_item(x, "arcade_game") for x in feed.get("arcade_games", [])],
        "completion_badges": [_stable_item(x, "completion_badge") for x in feed.get("completion_badges", [])],
        "unresolved_candidates": [_stable_item(x, "candidate") for x in feed.get("unresolved_candidates", [])],
    }


def _build_change_history(current: dict[str, Any], previous: Any, generated_at: str) -> list[dict[str, Any]]:
    old_history = previous.get("change_history", []) if isinstance(previous, dict) else []
    history = [x for x in old_history if isinstance(x, dict)][-MAX_HISTORY:] if isinstance(old_history, list) else []
    if not isinstance(previous, dict):
        return history

    groups = (
        ("programme_event", "programme_events"),
        ("skill_badge", "skill_badges"),
        ("arcade_game", "arcade_games"),
        ("completion_badge", "completion_badges"),
    )
    changes: list[dict[str, Any]] = []
    for entity_type, field in groups:
        before = previous.get(field, []) if isinstance(previous.get(field), list) else []
        after = current.get(field, []) if isinstance(current.get(field), list) else []
        old_map = {_identity(x, entity_type): x for x in before if isinstance(x, dict)}
        new_map = {_identity(x, entity_type): x for x in after if isinstance(x, dict)}
        for key, item in new_map.items():
            current_hash = _digest(_stable_item(item, entity_type))
            old_item = old_map.get(key)
            if old_item is None:
                change_type, previous_hash = "added", None
            else:
                previous_hash = _digest(_stable_item(old_item, entity_type))
                if current_hash == previous_hash:
                    continue
                change_type = "updated"
            changes.append({
                "entity_type": entity_type,
                "entity_id": key,
                "change_type": change_type,
                "title": _clean_text(item.get("title"), 180),
                "source_url": item.get("source_url") or item.get("badge_url") or (item.get("source_urls") or [None])[0],
                "verification_status": item.get("verification_status") or ("catalogue_verified" if item.get("verified") is True else "not_verified"),
                "previous_digest": previous_hash,
                "current_digest": current_hash,
                "detected_at": generated_at,
            })
        for key, item in old_map.items():
            if key not in new_map:
                changes.append({
                    "entity_type": entity_type,
                    "entity_id": key,
                    "change_type": "removed",
                    "title": _clean_text(item.get("title"), 180),
                    "source_url": item.get("source_url") or item.get("badge_url") or (item.get("source_urls") or [None])[0],
                    "verification_status": "no_longer_in_current_feed",
                    "previous_digest": _digest(_stable_item(item, entity_type)),
                    "current_digest": None,
                    "detected_at": generated_at,
                })
    history.extend(changes)
    return history[-MAX_HISTORY:]


def build_public_feed(
    events_data: Any,
    youtube_data: Any,
    generated_at: str | None = None,
    catalogue_data: Any = None,
    previous_data: Any = None,
) -> dict[str, Any]:
    generated = generated_at or utc_now()
    catalogue = catalogue_data if isinstance(catalogue_data, dict) else {}
    raw_skills = catalogue.get("entries") if isinstance(catalogue.get("entries"), list) else []
    raw_games = catalogue.get("game_entries") if isinstance(catalogue.get("game_entries"), list) else []
    raw_completions = catalogue.get("completion_badges") if isinstance(catalogue.get("completion_badges"), list) else []
    raw_candidates = catalogue.get("candidates") if isinstance(catalogue.get("candidates"), list) else []

    skill_badges = [x for x in (_catalogue_entry(item, "skill") for item in raw_skills) if x]
    arcade_games = [x for x in (_catalogue_entry(item, "game") for item in raw_games) if x]
    completion_badges = [x for x in (_catalogue_entry(item, "completion") for item in raw_completions) if x]
    candidates = [x for x in (_candidate(item) for item in raw_candidates) if x][:100]

    feed: dict[str, Any] = {
        # Keep v1 so deployed legacy consumers remain compatible; all additions are optional.
        "schema_version": 1,
        "generated_at": generated,
        "knowledge_version": "",
        "agent": "Arcade Intelligence",
        "programme_events": _event_records(events_data),
        "skill_badges": skill_badges[:500],
        "arcade_games": arcade_games[:500],
        "completion_badges": completion_badges[:200],
        "unresolved_candidates": candidates,
        "youtube_videos": _video_records(youtube_data),
        "catalogue_summary": {
            "catalogue_version": _clean_text(catalogue.get("version"), 40) or None,
            "last_run_at": _clean_text(catalogue.get("last_run_at"), 40) or None,
            "status": _clean_text(catalogue.get("status") or "unknown", 40),
            "verified_skill_badges": len(skill_badges),
            "verified_arcade_games": len(arcade_games),
            "verified_completion_badges": len(completion_badges),
            "unresolved_candidates": len(candidates),
        },
        "notice": (
            "Read-only public information. Badge catalogue labels are based on explicit visible page/image evidence. "
            "A catalogue entry does not prove current availability, eligibility or a point award. "
            "No point values are inferred. Programme announcements still require the existing source and review gates."
        ),
    }
    feed["knowledge_version"] = _digest(_material_projection(feed))[:16]
    feed["change_history"] = _build_change_history(feed, previous_data, generated)
    return feed


def write_public_feed() -> dict[str, Any]:
    previous = load_json(OUTPUT_FILE, {})
    payload = build_public_feed(
        load_json(EVENTS_FILE, {"events": []}),
        load_json(YOUTUBE_FILE, {"observations": []}),
        catalogue_data=load_json(CATALOGUE_FILE, {}),
        previous_data=previous,
    )
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_FILE.with_suffix(OUTPUT_FILE.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT_FILE)
    return payload


if __name__ == "__main__":
    result = write_public_feed()
    print(json.dumps({
        "status": "success",
        "knowledge_version": result["knowledge_version"],
        "programme_events": len(result["programme_events"]),
        "skill_badges": len(result["skill_badges"]),
        "arcade_games": len(result["arcade_games"]),
        "completion_badges": len(result["completion_badges"]),
        "unresolved_candidates": len(result["unresolved_candidates"]),
        "youtube_videos": len(result["youtube_videos"]),
        "changes_recorded": len(result["change_history"]),
        "generated_at": result["generated_at"],
    }, indent=2))
