#!/usr/bin/env python3
"""Merge public observations into conservative, evidence-linked Arcade events.

Scores are heuristic evidence indicators, not calibrated probabilities. A
community reply count never upgrades a claim to official confirmation.
"""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from event_classifier import classify_event, extract_explicit_point_mentions, extract_tier_mentions

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
COMMUNITY_FILE = DATA / "community-observations.json"
YOUTUBE_FILE = DATA / "youtube-observations.json"
EVENTS_FILE = DATA / "program-events.json"

STOP_WORDS = {"google", "skills", "skill", "arcade", "the", "new", "drop", "swag", "announcement", "update", "official", "programme", "program", "video", "cloud"}


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


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").casefold())
    return {word for word in words if word not in STOP_WORDS and len(word) > 1}


def _same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("event_type") != b.get("event_type"):
        return False
    ta, tb = title_tokens(str(a.get("title") or "")), title_tokens(str(b.get("title") or ""))
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    similarity = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    # Require a strong product/event token overlap; fuzzy title similarity alone
    # is deliberately insufficient for events with similar generic wording.
    return overlap >= 0.75 or (overlap >= 0.5 and similarity >= 0.72)


def _cluster(observations: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    ordered = sorted(observations, key=lambda x: (str(x.get("published_at") or ""), str(x.get("observation_id") or "")))
    for obs in ordered:
        placed = False
        for cluster in clusters:
            if any(_same_event(obs, member) for member in cluster):
                cluster.append(obs)
                placed = True
                break
        if not placed:
            clusters.append([obs])
    return clusters


def _source_priority(obs: dict[str, Any]) -> int:
    if obs.get("source_type") == "google_cloud_community" and obs.get("is_google_staff_author"):
        return 0
    if obs.get("source_type") == "youtube_video" and obs.get("publisher_type") == "google_official_channel":
        return 1
    if obs.get("source_type") == "google_cloud_community":
        return 2
    return 3


def build_events() -> dict[str, Any]:
    community = load_json(COMMUNITY_FILE, {"observations": []}).get("observations", [])
    youtube = load_json(YOUTUBE_FILE, {"observations": []}).get("observations", [])
    observations = [x for x in [*community, *youtube] if isinstance(x, dict) and x.get("title")]
    clusters = _cluster(observations)
    events: list[dict[str, Any]] = []

    for cluster in clusters:
        cluster.sort(key=_source_priority)
        primary = cluster[0]
        title = str(primary.get("title") or "Untitled programme event")
        body_parts = [str(x.get("text_excerpt") or "") for x in cluster]
        body = " ".join(body_parts)
        event_type = str(primary.get("event_type") or classify_event(title, body) or "unclassified")
        domains = sorted({str(x.get("source_domain") or "") for x in cluster if x.get("source_domain")})
        staff_sources = [x for x in cluster if x.get("source_type") == "google_cloud_community" and x.get("is_google_staff_author")]
        official_youtube = [x for x in cluster if x.get("source_type") == "youtube_video" and x.get("publisher_type") == "google_official_channel"]
        if staff_sources:
            verification = "officially_announced"
            score = 0.95
            explanation = "The originating Google Cloud Community post is attributed to a public account marked in the google_staff group."
        elif official_youtube:
            verification = "official_channel_coverage_unconfirmed"
            score = 0.72
            explanation = "A Google-owned channel has relevant video metadata, but this alone is not treated as a programme-specific official announcement."
        elif len(domains) >= 2:
            verification = "multi_source_corroborated_unverified"
            score = 0.68
            explanation = "More than one source domain discusses a similar claim, but no authoritative programme announcement was identified."
        elif any(x.get("source_type") == "google_cloud_community" for x in cluster):
            verification = "community_report_unverified"
            score = 0.48
            explanation = "The claim appears in a community topic but no Google staff author was identified. Replies do not count as independent confirmation."
        else:
            verification = "creator_report_unverified"
            score = 0.38
            explanation = "The claim appears in creator-video metadata only and needs official confirmation."

        source_records = []
        for item in cluster:
            source_records.append({
                "source_type": item.get("source_type"),
                "source_name": item.get("source_name"),
                "source_domain": item.get("source_domain"),
                "source_url": item.get("source_url"),
                "published_at": item.get("published_at"),
                "publisher_authority": item.get("publisher_authority") or item.get("publisher_type"),
                "is_google_staff_author": bool(item.get("is_google_staff_author", False)),
            })
        source_records = sorted(source_records, key=lambda x: (x.get("source_domain") or "", x.get("source_url") or ""))
        links = list(dict.fromkeys(str(x.get("source_url")) for x in cluster if x.get("source_url")))
        images = []
        for item in cluster:
            for image in item.get("image_references", []):
                if isinstance(image, dict) and image.get("url"):
                    images.append(image)
            if item.get("thumbnail_url"):
                images.append({"url": item["thumbnail_url"], "alt": "YouTube video thumbnail"})
        dedup_images = {x["url"]: x for x in images}
        event_id = hashlib.sha256((event_type + "|" + " ".join(sorted(title_tokens(title)))).encode("utf-8")).hexdigest()[:20]
        published_dates = [str(x.get("published_at")) for x in cluster if x.get("published_at")]
        community_replies = max([int(x.get("reply_count", 0)) for x in cluster if x.get("source_type") == "google_cloud_community"] or [0])
        positive_signals = sum(int(x.get("discussion_signals", {}).get("positive_report_phrase_hits", 0)) for x in cluster)
        negative_signals = sum(int(x.get("discussion_signals", {}).get("negative_or_uncertain_phrase_hits", 0)) for x in cluster)
        if negative_signals:
            score = max(0.1, score - min(0.2, negative_signals * 0.03))
        point_mentions = list(dict.fromkeys(snippet for x in cluster for snippet in extract_explicit_point_mentions(str(x.get("text_excerpt") or ""))))[:20]
        tier_mentions = sorted({tier for x in cluster for tier in extract_tier_mentions(str(x.get("text_excerpt") or ""))})
        content_fingerprints = sorted({hashlib.sha256(str(x.get("text_excerpt") or "").encode("utf-8")).hexdigest() for x in cluster if x.get("text_excerpt")})
        events.append({
            "event_id": event_id,
            "event_type": event_type,
            "title": title,
            "verification_status": verification,
            "evidence_score": round(score, 2),
            "evidence_score_note": "Heuristic evidence indicator, not a probability or guarantee.",
            "verification_explanation": explanation,
            "published_at": min(published_dates) if published_dates else None,
            "last_observed_at": max(str(x.get("last_seen_at") or "") for x in cluster),
            "source_count": len(links),
            "independent_source_domains": domains,
            "sources": source_records,
            "source_urls": links,
            "image_references": list(dedup_images.values())[:30],
            "explicit_point_mentions": point_mentions,
            "tier_mentions": tier_mentions,
            "community_reply_count": community_replies,
            "community_positive_phrase_hits": positive_signals,
            "community_negative_or_uncertain_phrase_hits": negative_signals,
            "availability_status": "not_assessed",
            "human_review_required": True,
            "observation_ids": [str(x.get("observation_id")) for x in cluster],
            "content_fingerprints": content_fingerprints,
        })

    events.sort(key=lambda x: (x.get("published_at") or "", x["event_id"]), reverse=True)
    payload = {
        "schema_version": 1,
        "agent": "Arcade Intelligence",
        "watcher_version": "1.3.0",
        "event_count": len(events),
        "verification_policy": "Only a Google Cloud Community first post whose public author metadata identifies the google_staff group is classified as officially_announced. Community replies and creator videos cannot independently establish official status.",
        "events": events[:500],
    }
    previous = load_json(EVENTS_FILE, {})
    if events != previous.get("events", []):
        payload["updated_at"] = utc_now()
    else:
        payload["updated_at"] = previous.get("updated_at")
    save_json(EVENTS_FILE, payload)
    return payload


if __name__ == "__main__":
    result = build_events()
    print(json.dumps({"status": "success", "events": result["event_count"], "verified_official_announcements": sum(1 for x in result["events"] if x["verification_status"] == "officially_announced")}, indent=2))
