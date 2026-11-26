#!/usr/bin/env python3
"""Merge public observations into conservative, evidence-linked Arcade events.

Evidence scores are heuristic indicators, not probabilities. Community replies,
forum permissions and creator commentary never independently establish an
official Google programme announcement.
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
REVIEW_DECISIONS_FILE = DATA / "review-decisions.json"
TRUSTED_AUTHOR_METHOD = "discourse_google_staff_group"

STOP_WORDS = {
    "google", "skills", "skill", "arcade", "the", "new", "drop", "swag",
    "announcement", "update", "official", "programme", "program", "video", "cloud",
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
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _read_observations(path: Path) -> list[dict[str, Any]]:
    data = load_json(path, {})
    if not isinstance(data, dict) or not isinstance(data.get("observations"), list):
        return []
    return [item for item in data["observations"] if isinstance(item, dict) and item.get("title")]


def _is_verified_google_staff_observation(item: dict[str, Any]) -> bool:
    """Require the exact evidence emitted by the hardened Discourse watcher."""
    return (
        item.get("source_type") == "google_cloud_community"
        and item.get("source_domain") == "discuss.google.dev"
        and str(item.get("source_url") or "").startswith("https://discuss.google.dev/t/")
        and item.get("is_google_staff_author") is True
        and item.get("publisher_authority") == "google_staff_group"
        and item.get("author_verification_method") == TRUSTED_AUTHOR_METHOD
    )


def approval_fingerprint(
    event_type: str,
    title: str,
    source_urls: list[str],
    content_fingerprints: list[str],
    point_mentions: list[str],
    tier_mentions: list[str],
) -> str:
    """Bind operator approval to the precise evidence version being reviewed."""
    payload = {
        "event_type": event_type,
        "title": title,
        "source_urls": sorted(set(source_urls)),
        "content_fingerprints": sorted(set(content_fingerprints)),
        "point_mentions": sorted(set(point_mentions)),
        "tier_mentions": sorted(set(tier_mentions)),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_review_decisions() -> list[dict[str, Any]]:
    payload = load_json(REVIEW_DECISIONS_FILE, {})
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return []
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return []
    return [item for item in decisions if isinstance(item, dict)]


def _valid_review_decision(
    event_id: str,
    fingerprint: str,
    source_urls: list[str],
    decisions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Accept only a specific decision tied to current evidence and a source URL."""
    for decision in reversed(decisions):
        if decision.get("event_id") != event_id:
            continue
        if decision.get("approval_fingerprint") != fingerprint:
            continue
        if decision.get("decision") not in {"approve", "reject", "hold"}:
            continue
        reviewed_at = decision.get("reviewed_at")
        source_url = decision.get("reviewed_source_url")
        if not isinstance(reviewed_at, str) or not source_url or source_url not in source_urls:
            continue
        try:
            parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            continue
        return decision
    return None


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").casefold())
    return {word for word in words if word not in STOP_WORDS and len(word) > 1}


def _same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("event_type") != b.get("event_type"):
        return False
    ta = title_tokens(str(a.get("title") or ""))
    tb = title_tokens(str(b.get("title") or ""))
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    similarity = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return overlap >= 0.75 or (overlap >= 0.5 and similarity >= 0.72)


def _cluster(observations: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    ordered = sorted(
        observations,
        key=lambda item: (str(item.get("published_at") or ""), str(item.get("observation_id") or "")),
    )
    for observation in ordered:
        placed = False
        for cluster in clusters:
            if any(_same_event(observation, member) for member in cluster):
                cluster.append(observation)
                placed = True
                break
        if not placed:
            clusters.append([observation])
    return clusters


def _source_priority(item: dict[str, Any]) -> int:
    if _is_verified_google_staff_observation(item):
        return 0
    if item.get("source_type") == "youtube_video" and item.get("publisher_type") == "google_official_channel":
        return 1
    if item.get("source_type") == "google_cloud_community":
        return 2
    return 3


def build_events() -> dict[str, Any]:
    observations = _read_observations(COMMUNITY_FILE) + _read_observations(YOUTUBE_FILE)
    review_decisions = _load_review_decisions()
    clusters = _cluster(observations)
    events: list[dict[str, Any]] = []

    for cluster in clusters:
        cluster.sort(key=_source_priority)
        primary = cluster[0]
        title = str(primary.get("title") or "Untitled programme event")
        body = " ".join(str(item.get("text_excerpt") or "") for item in cluster)
        event_type = str(primary.get("event_type") or classify_event(title, body) or "unclassified")
        domains = sorted({str(item.get("source_domain") or "") for item in cluster if item.get("source_domain")})
        trusted_staff_sources = [item for item in cluster if _is_verified_google_staff_observation(item)]
        official_youtube = [
            item for item in cluster
            if item.get("source_type") == "youtube_video"
            and item.get("publisher_type") == "google_official_channel"
        ]

        if trusted_staff_sources:
            verification = "officially_announced"
            score = 0.95
            explanation = (
                "The originating Google Cloud Community post author has the exact public "
                "google_staff group marker in Discourse user metadata. Forum moderator/admin "
                "privileges alone are not accepted as proof of Google employment."
            )
        elif official_youtube:
            verification = "official_channel_coverage_unconfirmed"
            score = 0.72
            explanation = (
                "A configured Google-owned YouTube channel has relevant public metadata, "
                "but channel ownership alone does not confirm a programme-specific announcement."
            )
        elif len(domains) >= 2:
            verification = "multi_source_corroborated_unverified"
            score = 0.68
            explanation = (
                "More than one source domain discusses a similar claim, but no trusted "
                "authoritative programme announcement was identified."
            )
        elif any(item.get("source_type") == "google_cloud_community" for item in cluster):
            verification = "community_report_unverified"
            score = 0.48
            explanation = (
                "The claim appears in a community topic, but no verified Google Staff group "
                "marker was identified on the originating post. Replies do not count as proof."
            )
        else:
            verification = "creator_report_unverified"
            score = 0.38
            explanation = "The claim appears in creator-video metadata only and needs official confirmation."

        source_records: list[dict[str, Any]] = []
        for item in cluster:
            source_records.append({
                "source_type": item.get("source_type"),
                "source_name": item.get("source_name"),
                "source_domain": item.get("source_domain"),
                "source_url": item.get("source_url"),
                "published_at": item.get("published_at"),
                "publisher_authority": item.get("publisher_authority") or item.get("publisher_type"),
                "is_google_staff_author": bool(item.get("is_google_staff_author", False)),
                "author_verification_method": item.get("author_verification_method"),
            })
        source_records.sort(key=lambda item: (item.get("source_domain") or "", item.get("source_url") or ""))

        links = list(dict.fromkeys(str(item.get("source_url")) for item in cluster if item.get("source_url")))
        images: list[dict[str, str]] = []
        for item in cluster:
            for image in item.get("image_references", []):
                if isinstance(image, dict) and image.get("url"):
                    images.append({"url": str(image["url"]), "alt": str(image.get("alt") or "")})
            if item.get("thumbnail_url"):
                images.append({"url": str(item["thumbnail_url"]), "alt": "YouTube video thumbnail"})
        dedup_images = {image["url"]: image for image in images}

        event_id = hashlib.sha256(
            (event_type + "|" + " ".join(sorted(title_tokens(title)))).encode("utf-8")
        ).hexdigest()[:20]
        published_dates = [str(item.get("published_at")) for item in cluster if item.get("published_at")]
        community_replies = max(
            [int(item.get("reply_count", 0)) for item in cluster if item.get("source_type") == "google_cloud_community"]
            or [0]
        )
        positive_signals = sum(
            int(item.get("discussion_signals", {}).get("positive_report_phrase_hits", 0)) for item in cluster
        )
        negative_signals = sum(
            int(item.get("discussion_signals", {}).get("negative_or_uncertain_phrase_hits", 0)) for item in cluster
        )
        if negative_signals:
            score = max(0.1, score - min(0.2, negative_signals * 0.03))

        point_mentions = list(dict.fromkeys(
            snippet
            for item in cluster
            for snippet in extract_explicit_point_mentions(str(item.get("text_excerpt") or ""))
        ))[:20]
        tier_mentions = sorted({
            tier
            for item in cluster
            for tier in extract_tier_mentions(str(item.get("text_excerpt") or ""))
        })
        content_fingerprints = sorted({
            hashlib.sha256(str(item.get("text_excerpt") or "").encode("utf-8")).hexdigest()
            for item in cluster if item.get("text_excerpt")
        })
        approval_hash = approval_fingerprint(
            event_type,
            title,
            links,
            content_fingerprints,
            point_mentions,
            tier_mentions,
        )
        trusted_source_urls = [
            str(item.get("source_url"))
            for item in trusted_staff_sources
            if item.get("source_url")
        ]
        review = _valid_review_decision(event_id, approval_hash, trusted_source_urls, review_decisions)
        review_status = str(review.get("decision")) if review else "pending"
        approved_for_notification = bool(review and review_status == "approve" and trusted_staff_sources)

        events.append({
            "event_id": event_id,
            "event_type": event_type,
            "title": title,
            "verification_status": verification,
            "evidence_score": round(score, 2),
            "evidence_score_note": "Heuristic evidence indicator, not a probability or guarantee.",
            "verification_explanation": explanation,
            "published_at": min(published_dates) if published_dates else None,
            "last_observed_at": max(str(item.get("last_seen_at") or "") for item in cluster),
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
            # Approval is stored separately and bound to this exact evidence version.
            "human_review_required": review_status == "pending",
            "notification_approved": approved_for_notification,
            "review_decision": review_status,
            "reviewed_at": review.get("reviewed_at") if review else None,
            "reviewed_source_url": review.get("reviewed_source_url") if review else None,
            "approval_fingerprint": approval_hash,
            "observation_ids": [str(item.get("observation_id")) for item in cluster],
            "content_fingerprints": content_fingerprints,
        })

    events.sort(key=lambda item: (item.get("published_at") or "", item["event_id"]), reverse=True)
    payload = {
        "schema_version": 1,
        "agent": "Arcade Intelligence",
        "watcher_version": "1.3.2",
        "event_count": len(events[:500]),
        "verification_policy": (
            "Only a Google Cloud Community first post whose public author metadata contains "
            "the exact google_staff group marker can be labelled officially_announced. "
            "Forum staff/admin/moderator flags, community replies and creator videos do not "
            "independently establish official status. Notifications require an operator decision "
            "in review-decisions.json bound to the current approval fingerprint and source URL."
        ),
        "events": events[:500],
    }
    previous = load_json(EVENTS_FILE, {})
    previous_events = previous.get("events", []) if isinstance(previous, dict) else []
    if events[:500] != previous_events:
        payload["updated_at"] = utc_now()
    else:
        payload["updated_at"] = previous.get("updated_at") if isinstance(previous, dict) else None
    save_json(EVENTS_FILE, payload)
    return payload


if __name__ == "__main__":
    result = build_events()
    print(json.dumps({
        "status": "success",
        "events": result["event_count"],
        "verified_official_announcements": sum(
            1 for item in result["events"] if item["verification_status"] == "officially_announced"
        ),
    }, indent=2))
