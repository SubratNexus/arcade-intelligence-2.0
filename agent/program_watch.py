#!/usr/bin/env python3
"""Polite public monitor for Google Cloud Community Arcade announcements.

Only public Discourse JSON on the explicit discuss.google.dev allowlist is read.
The watcher checks robots.txt, rate-limits requests, blocks redirects away from
its allowlist, caps response sizes and records replies as discussion signals,
not as proof of an official programme claim.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from event_classifier import classify_event, html_to_text

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
SOURCES_FILE = DATA / "sources.json"
STATE_FILE = DATA / "watcher-state.json"
OUTPUT_FILE = DATA / "community-observations.json"
RUN_FILE = DATA / "last-program-watch.json"
ALLOWED_HOSTS = {"discuss.google.dev"}
USER_AGENT = "ArcadeIntelligenceBot/1.3 (+https://github.com/SubratNexus/arcade-intelligence-2.0; public programme monitor)"
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 2_000_000
MAX_CATEGORY_TOPICS = int(os.getenv("ARCADE_WATCH_MAX_TOPICS", "40"))
MAX_TOPIC_FETCHES = int(os.getenv("ARCADE_WATCH_MAX_TOPIC_FETCHES", "12"))
MIN_REQUEST_GAP_SECONDS = 1.5

RELEVANCE_TERMS = (
    "google skills arcade", "skills arcade", "arcade", "swag", "prize counter",
    "arcade points", "bonus points", "arcade game", "arcade adventure",
    "arcade voyage", "arcade trail", "trooper", "ranger", "champion", "legend",
)
POSITIVE_DISCUSSION = (
    "received mine", "got mine", "available in prize counter", "i can see it",
    "showing in prize counter", "successfully claimed",
)
NEGATIVE_DISCUSSION = (
    "not available", "not showing", "out of stock", "unable to claim",
    "not received", "incorrect information", "rumour", "rumor", "fake",
)
GOOGLE_STAFF_GROUP = "google_staff"


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


def safe_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() in ALLOWED_HOSTS
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


def _safe_image_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a redirect before urllib sends a request to its destination."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if not safe_url(newurl):
            raise urllib.error.HTTPError(req.full_url, code, "Redirect destination blocked by host allowlist", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RobotsGate:
    def __init__(self) -> None:
        self.parsers: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last_request = 0.0
        self.opener = urllib.request.build_opener(_AllowlistedRedirectHandler())

    def _wait(self) -> None:
        gap = time.monotonic() - self.last_request
        if gap < MIN_REQUEST_GAP_SECONDS:
            time.sleep(MIN_REQUEST_GAP_SECONDS - gap)
        self.last_request = time.monotonic()

    def _read_url(self, url: str, accept: str = "application/json") -> tuple[bytes, str]:
        if not safe_url(url):
            raise ValueError("URL host is not allowlisted")
        self._wait()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                    final_url = response.geturl()
                    if not safe_url(final_url):
                        raise ValueError("Redirect to a non-allowlisted host was blocked")
                    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                    declared = int(response.headers.get("Content-Length") or 0)
                    if declared > MAX_RESPONSE_BYTES:
                        raise ValueError("Response exceeded configured size limit")
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise ValueError("Response exceeded configured size limit")
                    return payload, content_type
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            except urllib.error.URLError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("Public source request failed after retries")

    def allowed(self, url: str) -> bool:
        if not safe_url(url):
            return False
        host = urllib.parse.urlsplit(url).hostname or ""
        if host not in self.parsers:
            robots_url = f"https://{host}/robots.txt"
            try:
                raw, _ = self._read_url(robots_url, "text/plain")
                parser = urllib.robotparser.RobotFileParser(robots_url)
                parser.parse(raw.decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    parser = urllib.robotparser.RobotFileParser(robots_url)
                    parser.parse(["User-agent: *", "Allow: /"])
                else:
                    return False
            except Exception:
                return False
            self.parsers[host] = parser
        return self.parsers[host].can_fetch(USER_AGENT, url)

    def get_json(self, url: str) -> dict[str, Any]:
        if not self.allowed(url):
            raise PermissionError("robots.txt or host policy does not permit this request")
        raw, content_type = self._read_url(url)
        if content_type and "json" not in content_type:
            raise ValueError("Expected a JSON response from the public Discourse endpoint")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("The public endpoint did not return valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Unexpected JSON response shape")
        return data


class ImageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        src = values.get("src") or values.get("data-src") or ""
        if src:
            self.items.append({"url": src, "alt": values.get("alt", "")[:300]})


def extract_image_refs(html: str, base_url: str) -> list[dict[str, str]]:
    parser = ImageExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.items:
        try:
            url = urllib.parse.urljoin(base_url, item["url"])
            if not _safe_image_url(url) or url in seen:
                continue
            seen.add(url)
            result.append({"url": url, "alt": item["alt"]})
        except ValueError:
            continue
    return result[:20]


def _is_relevant(title: str, excerpt: str) -> bool:
    combined = f"{title} {excerpt}".casefold()
    return any(term in combined for term in RELEVANCE_TERMS)


def _user_is_google_staff(username: str, user_id: Any, users: list[dict[str, Any]], _post: dict[str, Any] | None = None) -> bool:
    """Require the explicit Google Staff group marker in public user metadata.

    Discourse staff/admin/moderator flags describe forum permissions; they do
    not establish that the author is a Google employee and are never accepted.
    """
    for user in users:
        if user_id is not None:
            matches_author = str(user.get("id")) == str(user_id)
        else:
            matches_author = bool(username) and user.get("username") == username
        if not matches_author:
            continue
        groups = user.get("groups") or []
        group_names = {
            str(group.get("name", "")).strip().casefold()
            for group in groups
            if isinstance(group, dict)
        }
        primary = str(user.get("primary_group_name") or "").strip().casefold()
        if primary == GOOGLE_STAFF_GROUP or GOOGLE_STAFF_GROUP in group_names:
            return True
    return False


def _discussion_signals(posts: list[dict[str, Any]]) -> dict[str, int]:
    positive = 0
    negative = 0
    for post in posts[1:]:
        text = html_to_text(str(post.get("cooked") or post.get("raw") or "")).casefold()
        positive += sum(1 for phrase in POSITIVE_DISCUSSION if phrase in text)
        negative += sum(1 for phrase in NEGATIVE_DISCUSSION if phrase in text)
    return {"positive_report_phrase_hits": positive, "negative_or_uncertain_phrase_hits": negative}


def _topic_observation(
    topic: dict[str, Any],
    detail: dict[str, Any],
    users: list[dict[str, Any]],
    source: dict[str, Any],
    first_seen: str,
) -> dict[str, Any] | None:
    stream = detail.get("post_stream") if isinstance(detail.get("post_stream"), dict) else {}
    posts = stream.get("posts") if isinstance(stream.get("posts"), list) else []
    first_post = next((post for post in posts if isinstance(post, dict) and post.get("post_number") == 1), None)
    if first_post is None:
        first_post = next((post for post in posts if isinstance(post, dict)), {})

    title = str(detail.get("title") or topic.get("title") or "").strip()
    cooked = str(first_post.get("cooked") or "")
    body = html_to_text(str(first_post.get("raw") or cooked))
    if not body:
        body = html_to_text(str(topic.get("excerpt") or ""))
    event_type = classify_event(title, body)
    if not event_type:
        return None

    username = str(first_post.get("username") or "")
    user_id = first_post.get("user_id")
    is_google_staff = _user_is_google_staff(username, user_id, users, first_post)
    verification_method = "discourse_google_staff_group" if is_google_staff else None

    topic_id = topic.get("id") or detail.get("id")
    if topic_id is None or not str(topic_id).isdigit():
        return None
    slug = str(topic.get("slug") or detail.get("slug") or "topic")
    topic_url = f"https://discuss.google.dev/t/{urllib.parse.quote(slug, safe='-')}/{topic_id}"
    replies = [post for post in posts if isinstance(post, dict)]
    reply_count = max(
        int(topic.get("reply_count") or 0),
        int(detail.get("reply_count") or 0),
        max(0, len(replies) - 1),
    )

    images = extract_image_refs(cooked, topic_url)
    topic_image = str(topic.get("image_url") or "")
    if topic_image and _safe_image_url(topic_image):
        images.insert(0, {"url": topic_image, "alt": "Topic preview image"})
    unique_images: list[dict[str, str]] = []
    seen_images: set[str] = set()
    for image in images:
        if image["url"] not in seen_images:
            seen_images.add(image["url"])
            unique_images.append(image)

    created_at = str(first_post.get("created_at") or topic.get("created_at") or "")
    last_activity = str(topic.get("last_posted_at") or detail.get("last_posted_at") or created_at)
    return {
        "observation_id": f"discourse:{topic_id}",
        "source_type": "google_cloud_community",
        "source_name": source.get("name", "Google Cloud Community"),
        "source_domain": "discuss.google.dev",
        "source_url": topic_url,
        "published_at": created_at,
        "last_activity_at": last_activity,
        "title": title[:300],
        "text_excerpt": body[:5000],
        "event_type": event_type,
        "publisher_authority": "google_staff_group" if is_google_staff else "community_member",
        "is_google_staff_author": bool(is_google_staff),
        "author_verification_method": verification_method,
        "reply_count": reply_count,
        "discussion_signals": _discussion_signals(replies),
        "image_references": unique_images[:20],
        "first_seen_at": first_seen,
        "last_seen_at": utc_now(),
        "source_note": (
            "Google Staff attribution is accepted only when the public Discourse user metadata "
            "contains the exact google_staff primary group, flair group or group membership. "
            "Generic staff/admin/moderator privileges are not treated as Google employment. "
            "Replies are discussion signals, not independent official confirmation."
        ),
    }


def run() -> int:
    started = utc_now()
    source_config = load_json(SOURCES_FILE, {})
    if not isinstance(source_config, dict):
        source_config = {}
    discourse_sources = [
        source for source in source_config.get("discourse_categories", [])
        if isinstance(source, dict) and source.get("enabled", True)
    ]
    state = load_json(STATE_FILE, {"schema_version": 1, "topics": {}})
    if not isinstance(state, dict):
        state = {"schema_version": 1, "topics": {}}
    state_topics = state.get("topics") if isinstance(state.get("topics"), dict) else {}
    original_topics = dict(state_topics)
    output = load_json(OUTPUT_FILE, {"schema_version": 1, "updated_at": None, "observations": []})
    if not isinstance(output, dict):
        output = {"schema_version": 1, "updated_at": None, "observations": []}
    existing_list = output.get("observations") if isinstance(output.get("observations"), list) else []
    existing = {
        item.get("observation_id"): item
        for item in existing_list
        if isinstance(item, dict) and item.get("observation_id")
    }

    stats = {
        "categories_checked": 0,
        "topics_seen": 0,
        "relevant_topics": 0,
        "topic_details_fetched": 0,
        "new_or_updated_observations": 0,
        "google_staff_observations": 0,
        "community_observations": 0,
        "images_referenced": 0,
    }
    errors: list[str] = []
    fetcher = RobotsGate()

    for source in discourse_sources:
        source_url = str(source.get("url") or "")
        if not safe_url(source_url):
            errors.append(f"{source.get('id', 'unknown-source')}: source URL is outside the allowlist")
            continue
        try:
            category_data = fetcher.get_json(source_url)
            stats["categories_checked"] += 1
            topic_list = category_data.get("topic_list") if isinstance(category_data.get("topic_list"), dict) else {}
            topics = topic_list.get("topics") if isinstance(topic_list.get("topics"), list) else []
            users = category_data.get("users") if isinstance(category_data.get("users"), list) else []
            for topic in topics[:MAX_CATEGORY_TOPICS]:
                if not isinstance(topic, dict):
                    continue
                stats["topics_seen"] += 1
                if not _is_relevant(str(topic.get("title") or ""), str(topic.get("excerpt") or "")):
                    continue
                stats["relevant_topics"] += 1
                topic_id = topic.get("id")
                if topic_id is None or not str(topic_id).isdigit():
                    continue
                observation_id = f"discourse:{topic_id}"
                activity = str(topic.get("last_posted_at") or topic.get("bumped_at") or topic.get("created_at") or "")
                previous = state_topics.get(str(topic_id), {})
                if previous.get("last_activity_at") == activity and observation_id in existing:
                    continue
                if stats["topic_details_fetched"] >= MAX_TOPIC_FETCHES:
                    continue

                slug = str(topic.get("slug") or "topic")
                detail_url = f"https://discuss.google.dev/t/{urllib.parse.quote(slug, safe='-')}/{topic_id}.json"
                try:
                    detail = fetcher.get_json(detail_url)
                    observation = _topic_observation(
                        topic, detail, users, source, previous.get("first_seen_at") or started
                    )
                    stats["topic_details_fetched"] += 1
                    state_topics[str(topic_id)] = {
                        "last_activity_at": activity,
                        "first_seen_at": previous.get("first_seen_at") or started,
                    }
                    if observation:
                        existing[observation_id] = observation
                        stats["new_or_updated_observations"] += 1
                        stats["images_referenced"] += len(observation.get("image_references", []))
                        if observation["is_google_staff_author"]:
                            stats["google_staff_observations"] += 1
                        else:
                            stats["community_observations"] += 1
                except Exception as exc:
                    errors.append(f"topic {topic_id}: {type(exc).__name__}: {str(exc)[:160]}")
        except Exception as exc:
            errors.append(f"{source.get('id', 'unknown-source')}: {type(exc).__name__}: {str(exc)[:180]}")

    observations = sorted(existing.values(), key=lambda item: str(item.get("published_at") or ""))[-500:]
    previous_observations = output.get("observations", [])
    output_updated_at = output.get("updated_at")
    if observations != previous_observations or output_updated_at is None:
        output_updated_at = utc_now()
        save_json(OUTPUT_FILE, {"schema_version": 1, "updated_at": output_updated_at, "observations": observations})

    bounded_topics = dict(list(state_topics.items())[-1000:])
    if bounded_topics != original_topics:
        save_json(
            STATE_FILE,
            {
                "schema_version": 1,
                "updated_at": utc_now(),
                "topics": bounded_topics,
                "youtube_channels": state.get("youtube_channels", {}),
            },
        )

    run_status = "success" if stats["categories_checked"] and not errors else (
        "partial" if stats["categories_checked"] else "failed"
    )
    run_payload = {
        "started_at": started,
        "finished_at": utc_now(),
        "status": run_status,
        "stats": stats,
        "errors": errors[:30],
        "notes": [
            "Only public, robots-allowed Discourse JSON endpoints are accessed.",
            "Google Staff attribution requires the exact public google_staff group marker.",
            "Community replies are discussion signals, not independent proof.",
            "Image URLs are recorded as references; image pixels are not downloaded or classified.",
        ],
    }
    save_json(RUN_FILE, run_payload)
    print(json.dumps(run_payload, ensure_ascii=False, indent=2))
    # A partial monitor run is visibly failed so GitHub Actions can report it.
    return 0 if run_status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(run())
