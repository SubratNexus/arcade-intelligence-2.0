#!/usr/bin/env python3
"""Create a small, read-only JSON feed for Arcade Journey Tracker.

Only explicitly selected public programme/video fields are exported. Internal
observation identifiers, review fingerprints, author account IDs, and local
state are deliberately excluded.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
EVENTS_FILE = DATA / "program-events.json"
YOUTUBE_FILE = DATA / "youtube-observations.json"
OUTPUT_FILE = DATA / "public-feed.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _safe_public_url(value: Any, allowed_hosts: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.username or parsed.password:
        return None
    return value


def build_public_feed(
    events_data: Any,
    youtube_data: Any,
    generated_at: str | None = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    raw_events = events_data.get("events", []) if isinstance(events_data, dict) else []
    for event in raw_events if isinstance(raw_events, list) else []:
        if not isinstance(event, dict) or not str(event.get("title") or "").strip():
            continue
        source_urls = [
            safe for safe in (
                _safe_public_url(url, {"discuss.google.dev", "youtube.com", "www.youtube.com"})
                for url in (event.get("source_urls") if isinstance(event.get("source_urls"), list) else [])
            ) if safe
        ]
        sources: list[dict[str, Any]] = []
        for source in event.get("sources", []) if isinstance(event.get("sources"), list) else []:
            if not isinstance(source, dict):
                continue
            host = str(source.get("source_domain") or "")
            safe_url = _safe_public_url(source.get("source_url"), {"discuss.google.dev", "youtube.com", "www.youtube.com"})
            if not safe_url:
                continue
            sources.append({
                "source_type": str(source.get("source_type") or "unknown")[:60],
                "source_name": str(source.get("source_name") or "Public source")[:120],
                "source_domain": host[:120],
                "source_url": safe_url,
                "published_at": source.get("published_at"),
                "publisher_authority": str(source.get("publisher_authority") or "unknown")[:80],
                "is_google_staff_author": source.get("is_google_staff_author") is True,
                "author_verification_method": str(source.get("author_verification_method") or "unknown")[:80],
            })
        events.append({
            "event_id": str(event.get("event_id") or "")[:80],
            "event_type": str(event.get("event_type") or "unclassified")[:80],
            "title": str(event.get("title") or "")[:300],
            "verification_status": str(event.get("verification_status") or "unverified")[:80],
            "evidence_score": event.get("evidence_score") if isinstance(event.get("evidence_score"), (int, float)) else None,
            "verification_explanation": str(event.get("verification_explanation") or "")[:1000],
            "published_at": event.get("published_at"),
            "last_observed_at": event.get("last_observed_at"),
            "source_count": len(source_urls),
            "source_urls": list(dict.fromkeys(source_urls))[:10],
            "sources": sources[:10],
            "explicit_point_mentions": [str(x)[:200] for x in event.get("explicit_point_mentions", [])[:20]] if isinstance(event.get("explicit_point_mentions"), list) else [],
            "tier_mentions": [str(x)[:80] for x in event.get("tier_mentions", [])[:20]] if isinstance(event.get("tier_mentions"), list) else [],
            "human_review_required": event.get("human_review_required") is not False,
            "notification_approved": event.get("notification_approved") is True,
            "review_decision": str(event.get("review_decision") or "pending")[:30],
        })

    videos: list[dict[str, Any]] = []
    raw_videos = youtube_data.get("observations", []) if isinstance(youtube_data, dict) else []
    for video in raw_videos if isinstance(raw_videos, list) else []:
        if not isinstance(video, dict) or not str(video.get("title") or "").strip():
            continue
        source_url = _safe_public_url(video.get("source_url"), {"youtube.com", "www.youtube.com"})
        if not source_url:
            continue
        thumbnail = _safe_public_url(video.get("thumbnail_url"), {"i.ytimg.com", "img.youtube.com"})
        videos.append({
            "title": str(video.get("title") or "")[:300],
            "source_name": str(video.get("source_name") or "YouTube channel")[:120],
            "source_url": source_url,
            "published_at": video.get("published_at"),
            "publisher_type": str(video.get("publisher_type") or "unknown")[:80],
            "event_type": str(video.get("event_type") or "unclassified")[:80],
            "matched_keywords": [str(x)[:100] for x in video.get("matched_keywords", [])[:20]] if isinstance(video.get("matched_keywords"), list) else [],
            "thumbnail_url": thumbnail,
        })

    events.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    videos.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return {
        "schema_version": 1,
        "generated_at": generated_at or utc_now(),
        "agent": "Arcade Intelligence",
        "programme_events": events[:300],
        "youtube_videos": videos[:100],
        "notice": "Read-only public information. Verify important details at the original source. Evidence scores are heuristic indicators, not probabilities. An official publisher does not automatically make every video a programme announcement.",
    }


def write_public_feed() -> dict[str, Any]:
    payload = build_public_feed(
        load_json(EVENTS_FILE, {"events": []}),
        load_json(YOUTUBE_FILE, {"observations": []}),
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
        "programme_events": len(result["programme_events"]),
        "youtube_videos": len(result["youtube_videos"]),
        "generated_at": result["generated_at"],
    }, indent=2))
