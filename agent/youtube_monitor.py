#!/usr/bin/env python3
"""Monitor allowlisted Google Cloud YouTube channels through public Atom feeds.

No API key, Google Cloud project, page scraping, captions, or transcripts are used.
The feed endpoint is public but not a guaranteed API; failures are recorded and
existing observations are preserved so a temporary feed outage does not erase data.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from event_classifier import classify_event

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
SOURCES_FILE = DATA / "sources.json"
STATE_FILE = DATA / "watcher-state.json"
OUTPUT_FILE = DATA / "youtube-observations.json"
RUN_FILE = DATA / "last-youtube-run.json"
FEED_BASE = "https://www.youtube.com/feeds/videos.xml"
ALLOWED_HOSTS = {"youtube.com", "www.youtube.com"}
OFFICIAL_GOOGLE_CHANNELS = {
    "UCJS9pqu9BzkAMNTmzNMNhvg": "Google Cloud Tech",
    "UCTMRxtyHoE3LPcrl-kT4AQQ": "Google Cloud",
}
TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 1_000_000
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"


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


def _safe_feed_url(url: str, expected_channel_id: str | None = None) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        channel_ids = query.get("channel_id", [])
        return (
            parsed.scheme == "https"
            and parsed.hostname in ALLOWED_HOSTS
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and parsed.path == "/feeds/videos.xml"
            and set(query) == {"channel_id"}
            and len(channel_ids) == 1
            and channel_ids[0] in OFFICIAL_GOOGLE_CHANNELS
            and (expected_channel_id is None or channel_ids[0] == expected_channel_id)
        )
    except (ValueError, TypeError):
        return False


class _AllowlistedFeedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not allow a feed redirect to send the monitor to another host/path."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if not _safe_feed_url(newurl):
            raise urllib.error.HTTPError(req.full_url, code, "YouTube feed redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_FEED_OPENER = urllib.request.build_opener(_AllowlistedFeedRedirectHandler())


def fetch_feed(channel_id: str) -> bytes:
    if channel_id not in OFFICIAL_GOOGLE_CHANNELS:
        raise ValueError("YouTube channel is not in the official allowlist")
    url = f"{FEED_BASE}?{urllib.parse.urlencode({'channel_id': channel_id})}"
    if not _safe_feed_url(url, channel_id):
        raise ValueError("YouTube Atom feed URL failed the allowlist check")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ArcadeIntelligenceBot/2.0 (+public-feed-monitor)", "Accept": "application/atom+xml, application/xml, text/xml"},
    )
    for attempt in range(3):
        try:
            with _FEED_OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
                if not _safe_feed_url(response.geturl(), channel_id):
                    raise ValueError("YouTube feed response URL failed the allowlist check")
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                if content_type and not any(token in content_type for token in ("xml", "atom")):
                    raise ValueError("YouTube feed returned an unexpected content type")
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > MAX_RESPONSE_BYTES:
                    raise ValueError("YouTube feed exceeded the response-size limit")
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ValueError("YouTube feed exceeded the response-size limit")
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError(f"YouTube Atom feed returned HTTP {exc.code}") from None
            time.sleep(2 ** attempt)
        except urllib.error.URLError:
            if attempt == 2:
                raise RuntimeError("YouTube Atom feed connection failed after retries") from None
            time.sleep(2 ** attempt)
    raise RuntimeError("YouTube Atom feed request failed")


def parse_feed(payload: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("YouTube Atom feed contained invalid XML") from exc
    entries: list[dict[str, str]] = []
    for entry in root.findall(f"{ATOM}entry"):
        video_id = (entry.findtext(f"{YT}videoId") or "").strip()
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        published = (entry.findtext(f"{ATOM}published") or "").strip()
        media_group = entry.find(f"{MEDIA}group")
        description = ""
        thumbnail = ""
        if media_group is not None:
            description = (media_group.findtext(f"{MEDIA}description") or "")[:6000]
            thumbnail_node = media_group.find(f"{MEDIA}thumbnail")
            if thumbnail_node is not None:
                thumbnail = str(thumbnail_node.attrib.get("url") or "")
        if not VIDEO_ID_RE.fullmatch(video_id) or not title:
            continue
        entries.append({
            "video_id": video_id,
            "title": title[:300],
            "description": description,
            "published_at": published,
            "thumbnail_url": thumbnail,
        })
    return entries[:15]


def _matches_keywords(title: str, description: str, keywords: list[str]) -> list[str]:
    haystack = f"{title} {description}".casefold()
    return [str(word) for word in keywords if str(word).casefold() in haystack]


def run() -> int:
    started = utc_now()
    source_config = load_json(SOURCES_FILE, {})
    if not isinstance(source_config, dict):
        source_config = {}
    configured = source_config.get("youtube_channels", [])
    channels = [
        item for item in configured
        if isinstance(item, dict)
        and item.get("enabled", True)
        and str(item.get("channel_id") or "") in OFFICIAL_GOOGLE_CHANNELS
    ]

    output = load_json(OUTPUT_FILE, {"schema_version": 1, "updated_at": None, "observations": []})
    if not isinstance(output, dict):
        output = {"schema_version": 1, "updated_at": None, "observations": []}
    old_list = output.get("observations") if isinstance(output.get("observations"), list) else []
    observations = {
        item.get("observation_id"): item
        for item in old_list
        if isinstance(item, dict) and item.get("observation_id")
    }

    state = load_json(STATE_FILE, {"schema_version": 1, "topics": {}, "youtube_channels": {}})
    if not isinstance(state, dict):
        state = {"schema_version": 1, "topics": {}, "youtube_channels": {}}
    channel_state = state.get("youtube_channels")
    if not isinstance(channel_state, dict):
        channel_state = {}
    channel_state = dict(channel_state)

    if not channels:
        result = {
            "started_at": started, "finished_at": utc_now(), "status": "disabled",
            "channels_configured": 0, "channels_succeeded": 0,
            "videos_checked": 0, "relevant_videos": 0, "errors": [],
            "note": "No enabled allowlisted Google Cloud YouTube channels are configured.",
        }
        save_json(RUN_FILE, result)
        print(json.dumps(result, indent=2))
        return 0

    default_keywords = source_config.get("youtube_keywords") or [
        "Google Skills Arcade", "Skills Arcade", "Arcade swag", "Arcade points",
        "Prize Counter", "Arcade game", "bonus Arcade points", "Arcade Build-a-thon",
    ]
    errors: list[str] = []
    succeeded = 0
    checked = 0
    relevant = 0
    now = utc_now()

    for channel in channels:
        channel_id = str(channel["channel_id"])
        channel_key = str(channel.get("id") or channel_id)
        try:
            entries = parse_feed(fetch_feed(channel_id))
            succeeded += 1
            checked += len(entries)
            keywords = channel.get("keywords") or default_keywords
            for video in entries:
                title = video["title"]
                description = video["description"]
                matched = _matches_keywords(title, description, list(keywords))
                event_type = classify_event(title, description)
                if not matched and not event_type:
                    continue
                relevant += 1
                video_id = video["video_id"]
                observation_id = f"youtube:{video_id}"
                previous = observations.get(observation_id, {})
                canonical_url = f"https://www.youtube.com/watch?v={video_id}"
                thumbnail = video["thumbnail_url"]
                if thumbnail:
                    parsed_thumb = urllib.parse.urlsplit(thumbnail)
                    if parsed_thumb.scheme != "https" or parsed_thumb.hostname not in {"i.ytimg.com", "img.youtube.com"}:
                        thumbnail = ""
                fingerprint_payload = json.dumps({
                    "title": title, "description": description,
                    "published_at": video["published_at"], "thumbnail": thumbnail,
                }, sort_keys=True, ensure_ascii=False)
                fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
                changed = previous.get("metadata_fingerprint") != fingerprint
                observations[observation_id] = {
                    "observation_id": observation_id,
                    "source_type": "youtube_video",
                    "source_name": str(channel.get("name") or OFFICIAL_GOOGLE_CHANNELS[channel_id]),
                    "source_domain": "youtube.com",
                    "source_url": canonical_url,
                    "video_id": video_id,
                    "channel_id": channel_id,
                    "publisher_type": "google_official_channel",
                    "published_at": video["published_at"],
                    "title": title,
                    "text_excerpt": description,
                    "event_type": event_type,
                    "matched_keywords": matched,
                    "thumbnail_url": thumbnail,
                    "first_seen_at": previous.get("first_seen_at") or started,
                    "last_seen_at": now if changed else previous.get("last_seen_at", started),
                    "metadata_fingerprint": fingerprint,
                    "source_note": "Public title, publication date and optional media metadata read from the channel's public YouTube Atom feed. No API key, page scraping, captions or transcripts are used.",
                }
            channel_state[channel_key] = {"last_success_at": now, "feed_method": "public_atom"}
        except Exception as exc:
            # Exception text can contain remote response details; only type is recorded.
            errors.append(f"{channel_key}: {type(exc).__name__}: {str(exc)[:180]}")

    new_list = sorted(observations.values(), key=lambda item: str(item.get("published_at") or ""))[-300:]
    if new_list != old_list or output.get("updated_at") is None:
        save_json(OUTPUT_FILE, {"schema_version": 1, "updated_at": utc_now(), "observations": new_list})

    state["schema_version"] = 1
    state["youtube_channels"] = dict(list(channel_state.items())[-100:])
    save_json(STATE_FILE, state)

    if errors and succeeded:
        status = "partial"
    elif errors and not succeeded:
        status = "unavailable"
    else:
        status = "success"
    result = {
        "started_at": started,
        "finished_at": utc_now(),
        "status": status,
        "channels_configured": len(channels),
        "channels_succeeded": succeeded,
        "videos_checked": checked,
        "relevant_videos": relevant,
        "errors": errors[:20],
        "note": "YouTube's public channel Atom feed is used without an API key. It is an undocumented public endpoint and may change or become unavailable; previous observations are preserved on errors.",
    }
    save_json(RUN_FILE, result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
