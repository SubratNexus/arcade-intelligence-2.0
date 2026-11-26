#!/usr/bin/env python3
"""Monitor configured YouTube channels using the official YouTube Data API v3.

This module reads public video metadata only. It does not scrape YouTube pages,
use RSS feeds, download captions or retrieve private material. The API key is
optional; without it the monitor reports disabled and makes no API requests.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
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
API_BASE = "https://www.googleapis.com/youtube/v3"
ALLOWED_HOST = "www.googleapis.com"
OFFICIAL_GOOGLE_CHANNELS = {
    "UCJS9pqu9BzkAMNTmzNMNhvg": "Google Cloud Tech",
    "UCTMRxtyHoE3LPcrl-kT4AQQ": "Google Cloud",
}
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1_500_000


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


def _safe_api_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == ALLOWED_HOST
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


class _AllowlistedYouTubeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent the API key query string being redirected to another host."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if not _safe_api_url(newurl):
            raise urllib.error.HTTPError(req.full_url, code, "YouTube API redirect destination blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_API_OPENER = urllib.request.build_opener(_AllowlistedYouTubeRedirectHandler())


def api_get(endpoint: str, params: dict[str, str]) -> dict[str, Any]:
    if endpoint not in {"channels", "playlistItems"}:
        raise ValueError("Unsupported YouTube Data API endpoint")
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    if not _safe_api_url(url):
        raise ValueError("YouTube API request host is not allowlisted")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ArcadeIntelligenceBot/1.3", "Accept": "application/json"},
    )
    raw = b""
    for attempt in range(3):
        try:
            with _API_OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
                if not _safe_api_url(response.geturl()):
                    raise ValueError("Unexpected redirect from YouTube API")
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                if content_type and "json" not in content_type:
                    raise ValueError("YouTube API returned a non-JSON response")
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > MAX_RESPONSE_BYTES:
                    raise ValueError("YouTube API response exceeded size limit")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("YouTube API response exceeded size limit")
                break
        except urllib.error.HTTPError as exc:
            # Never include the request URL: it carries the API key in its query.
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError(f"YouTube Data API returned HTTP {exc.code}") from None
            time.sleep(2 ** attempt)
        except urllib.error.URLError:
            if attempt == 2:
                raise RuntimeError("YouTube Data API connection failed after retries") from None
            time.sleep(2 ** attempt)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("YouTube Data API returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Unexpected YouTube API response shape")
    if data.get("error"):
        error_code = data.get("error", {}).get("code", "unknown")
        raise RuntimeError(f"YouTube Data API reported an error (code {error_code})")
    return data


def _matches_keywords(title: str, description: str, keywords: list[str]) -> list[str]:
    haystack = f"{title} {description}".casefold()
    return [word for word in keywords if str(word).casefold() in haystack]


def run() -> int:
    started = utc_now()
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    source_config = load_json(SOURCES_FILE, {})
    if not isinstance(source_config, dict):
        source_config = {}
    channels = [
        item for item in source_config.get("youtube_channels", [])
        if isinstance(item, dict) and item.get("enabled", True)
    ]
    output = load_json(OUTPUT_FILE, {"schema_version": 1, "updated_at": None, "observations": []})
    if not isinstance(output, dict):
        output = {"schema_version": 1, "updated_at": None, "observations": []}
    output_list = output.get("observations") if isinstance(output.get("observations"), list) else []
    observations = {
        item.get("observation_id"): item
        for item in output_list
        if isinstance(item, dict) and item.get("observation_id")
    }
    state = load_json(STATE_FILE, {"schema_version": 1, "topics": {}, "youtube_channels": {}})
    if not isinstance(state, dict):
        state = {"schema_version": 1, "topics": {}, "youtube_channels": {}}
    existing_channel_state = state.get("youtube_channels")
    if not isinstance(existing_channel_state, dict):
        existing_channel_state = {}
    # Preserve an independent snapshot so cache updates are actually persisted.
    channel_state = copy.deepcopy(existing_channel_state)
    old_channel_state = copy.deepcopy(channel_state)

    if not api_key:
        payload = {
            "started_at": started,
            "finished_at": utc_now(),
            "status": "disabled",
            "channels_configured": len(channels),
            "videos_checked": 0,
            "relevant_videos": 0,
            "errors": [],
            "note": "YOUTUBE_API_KEY is not configured. No YouTube request was made. YouTube RSS and page scraping are intentionally not used.",
        }
        save_json(RUN_FILE, payload)
        print(json.dumps(payload, indent=2))
        return 0

    if not channels:
        payload = {
            "started_at": started,
            "finished_at": utc_now(),
            "status": "disabled",
            "channels_configured": 0,
            "videos_checked": 0,
            "relevant_videos": 0,
            "errors": [],
            "note": "No enabled YouTube channels are configured.",
        }
        save_json(RUN_FILE, payload)
        print(json.dumps(payload, indent=2))
        return 0

    default_keywords = source_config.get("youtube_keywords") or [
        "Google Skills Arcade", "Skills Arcade", "Arcade swag", "Arcade points",
        "Prize Counter", "Arcade game", "bonus Arcade points",
    ]
    errors: list[str] = []
    videos_checked = 0
    relevant_videos = 0

    for channel in channels:
        channel_id = str(channel.get("channel_id") or "").strip()
        if not channel_id.startswith("UC") or len(channel_id) < 20:
            errors.append(f"{channel.get('id', 'channel')}: invalid YouTube channel ID")
            continue
        channel_key = str(channel.get("id") or channel_id)
        cached = channel_state.get(channel_key, {})
        if not isinstance(cached, dict):
            cached = {}
        try:
            uploads_playlist = str(cached.get("uploads_playlist_id") or channel.get("uploads_playlist_id") or "")
            if not uploads_playlist:
                channel_info = api_get("channels", {
                    "part": "contentDetails", "id": channel_id, "key": api_key,
                })
                items = channel_info.get("items") or []
                if not items:
                    raise RuntimeError("YouTube channel was not found by the Data API")
                uploads_playlist = str(
                    items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") or ""
                )
                if not uploads_playlist:
                    raise RuntimeError("Uploads playlist was not returned for this channel")
                cached["uploads_playlist_id"] = uploads_playlist

            playlist = api_get("playlistItems", {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": "25",
                "key": api_key,
            })
            videos_checked += len(playlist.get("items") or [])
            keywords = channel.get("keywords") or default_keywords
            for item in playlist.get("items") or []:
                snippet = item.get("snippet") or {}
                resource = snippet.get("resourceId") or {}
                video_id = str(resource.get("videoId") or "")
                if not video_id:
                    continue
                title = str(snippet.get("title") or "").strip()
                description = str(snippet.get("description") or "")[:6000]
                matched = _matches_keywords(title, description, list(keywords))
                event_type = classify_event(title, description)
                if not matched and not event_type:
                    continue
                relevant_videos += 1
                thumbnails = snippet.get("thumbnails") or {}
                thumbnail = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
                observation_id = f"youtube:{video_id}"
                previous = observations.get(observation_id, {})
                metadata_payload = json.dumps({
                    "title": title,
                    "description": description,
                    "published_at": snippet.get("publishedAt"),
                    "thumbnail": thumbnail.get("url"),
                }, sort_keys=True, ensure_ascii=False)
                metadata_fingerprint = hashlib.sha256(metadata_payload.encode("utf-8")).hexdigest()
                changed = previous.get("metadata_fingerprint") != metadata_fingerprint
                observations[observation_id] = {
                    "observation_id": observation_id,
                    "source_type": "youtube_video",
                    "source_name": str(channel.get("name") or snippet.get("channelTitle") or "YouTube channel"),
                    "source_domain": "youtube.com",
                    "source_url": f"https://www.youtube.com/watch?v={urllib.parse.quote(video_id)}",
                    "video_id": video_id,
                    "channel_id": channel_id,
                    "publisher_type": "google_official_channel" if channel_id in OFFICIAL_GOOGLE_CHANNELS else "community_creator",
                    "published_at": str(snippet.get("publishedAt") or ""),
                    "title": title[:300],
                    "text_excerpt": description,
                    "event_type": event_type,
                    "matched_keywords": matched,
                    "thumbnail_url": str(thumbnail.get("url") or ""),
                    "first_seen_at": previous.get("first_seen_at") or started,
                    "last_seen_at": utc_now() if changed else previous.get("last_seen_at", started),
                    "metadata_fingerprint": metadata_fingerprint,
                    "source_note": "Public video title, description, publication date and thumbnail reference obtained through the official YouTube Data API. Captions/transcripts are not downloaded.",
                }
            channel_state[channel_key] = cached
        except Exception as exc:
            # Never emit request URLs: YouTube API keys are query parameters.
            errors.append(f"{channel_key}: {type(exc).__name__}")

    final_observations = sorted(
        observations.values(), key=lambda item: str(item.get("published_at") or "")
    )[-300:]
    previous_observations = output.get("observations", [])
    if final_observations != previous_observations or output.get("updated_at") is None:
        save_json(OUTPUT_FILE, {
            "schema_version": 1,
            "updated_at": utc_now(),
            "observations": final_observations,
        })

    if channel_state != old_channel_state:
        state["youtube_channels"] = dict(list(channel_state.items())[-100:])
        state["schema_version"] = 1
        save_json(STATE_FILE, state)

    status = "success" if not errors else ("partial" if relevant_videos or videos_checked else "failed")
    payload = {
        "started_at": started,
        "finished_at": utc_now(),
        "status": status,
        "channels_configured": len(channels),
        "videos_checked": videos_checked,
        "relevant_videos": relevant_videos,
        "errors": errors[:20],
        "quota_note": "This monitor uses channels.list only when an uploads playlist ID is not cached, then playlistItems.list. The official playlistItems.list method has a documented quota cost of 1 unit per call.",
    }
    save_json(RUN_FILE, payload)
    print(json.dumps(payload, indent=2))
    return 0 if status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(run())
