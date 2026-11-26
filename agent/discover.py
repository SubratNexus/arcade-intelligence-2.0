#!/usr/bin/env python3
"""Arcade Intelligence: polite public-catalogue discovery for Google Skills.

This job only visits public, robots-allowed pages on explicitly allowlisted Google
Skills hosts. It never logs in, accesses private profiles, or calls undocumented APIs.
This edition uses OCR and explicit page labels only; it makes no external AI API calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.robotparser
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "agent-data"
STATE_FILE = DATA / "state.json"
CATALOGUE_FILE = DATA / "catalogue.json"
LAST_RUN_FILE = DATA / "last-run.json"

USER_AGENT = "ArcadeIntelligenceBot/1.1 (+https://github.com/SubratNexus/arcade-intelligence-2.0; public catalogue discovery)"
ALLOWED_PAGE_HOSTS = {"skills.google", "www.skills.google", "go.skills.google", "cloudskillsboost.google", "www.cloudskillsboost.google"}
ALLOWED_IMAGE_HOSTS = {
    "skills.google", "go.skills.google", "storage.googleapis.com",
    "googleusercontent.com", "gstatic.com", "cloudskillsboost.google", "www.cloudskillsboost.google", "cdn.qwiklabs.com",
}
SEEDS = [
    "https://skills.google/",
    "https://skills.google/catalog",
    "https://go.skills.google/arcade",
]
MAX_PAGES = int(os.getenv("ARCADE_MAX_PAGES", "60"))
MAX_IMAGES = int(os.getenv("ARCADE_MAX_IMAGES", "24"))
MAX_IMAGE_BYTES = 1_200_000
MAX_PAGE_BYTES = 2_000_000
REQUEST_TIMEOUT = 15
MIN_DELAY_SECONDS = 1.25

SKILL_LABEL = re.compile(r"\bSKILL\s+BADGE\b", re.I)
GAME_LABEL = re.compile(r"\b(?:ARCADE\s+GAME|GAME\s+BADGE)\b", re.I)
COMPLETION_LABEL = re.compile(r"\bCOMPLETION\s+BADGE\b", re.I)
BADGE_WORDS = re.compile(r"\b(?:badge|arcade|skill)\b", re.I)
TITLE_NOISE = re.compile(r"\b(?:skill\s+badge|game\s+badge|completion\s+badge|badge\s+image|image|icon|logo)\b", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(path)


def host_allowed(url: str, image: bool = False) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
        hosts = ALLOWED_IMAGE_HOSTS if image else ALLOWED_PAGE_HOSTS
        return u.scheme == "https" and (u.hostname or "").lower() in hosts and not u.username and not u.password and not u.port
    except Exception:
        return False


class Fetcher:
    def __init__(self) -> None:
        self.last_request = 0.0
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.robots_sitemaps: dict[str, list[str]] = {}
        self.delays: dict[str, float] = {}
        self.blocked_hosts: set[str] = set()

    def _polite_wait(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).hostname or ""
        delay = self.delays.get(host, MIN_DELAY_SECONDS)
        gap = time.monotonic() - self.last_request
        if gap < delay:
            time.sleep(delay - gap)
        self.last_request = time.monotonic()

    def request(self, url: str, max_bytes: int = MAX_PAGE_BYTES, image: bool = False) -> tuple[bytes, str]:
        if not host_allowed(url, image=image):
            raise ValueError("Host is not allowlisted")
        self._polite_wait(url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml,image/avif,image/webp,image/png,image/jpeg,*/*;q=0.5"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            final_url = resp.geturl()
            if not host_allowed(final_url, image=image):
                raise ValueError("Redirect to a non-allowlisted host blocked")
            mime = (resp.headers.get("Content-Type") or "").split(";")[0].lower()
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                raise ValueError("Response exceeds size limit")
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("Response exceeds size limit")
            return data, mime

    def robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        host = urllib.parse.urlsplit(url).hostname or ""
        if host in self.blocked_hosts:
            return None
        if host in self.robots:
            return self.robots[host]
        robots_url = f"https://{host}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        try:
            data, _ = self.request(robots_url, max_bytes=512_000, image=host in ALLOWED_IMAGE_HOSTS)
            text = data.decode("utf-8", "replace")
            rp.parse(text.splitlines())
            try:
                delay = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*") or MIN_DELAY_SECONDS
                self.delays[host] = max(MIN_DELAY_SECONDS, min(float(delay), 10.0))
            except Exception:
                self.delays[host] = MIN_DELAY_SECONDS
            self.robots_sitemaps[host] = [
                line.split(":", 1)[1].strip()
                for line in text.splitlines()
                if line.lower().startswith("sitemap:") and ":" in line
            ]
            self.robots[host] = rp
            return rp
        except urllib.error.HTTPError as exc:
            # An explicit 404 means no robots policy file is published. Other
            # HTTP errors fail closed rather than guessing permission.
            if exc.code == 404:
                rp.parse(["User-agent: *", "Allow: /"])
                self.robots[host] = rp
                self.robots_sitemaps[host] = []
                self.delays[host] = MIN_DELAY_SECONDS
                return rp
            self.blocked_hosts.add(host)
            return None
        except Exception:
            self.blocked_hosts.add(host)
            return None

    def allowed(self, url: str, image: bool = False) -> bool:
        if not host_allowed(url, image=image):
            return False
        rp = self.robots_for(url)
        return bool(rp and rp.can_fetch(USER_AGENT, url))

    def crawl_delay(self, url: str) -> float:
        host = urllib.parse.urlsplit(url).hostname or ""
        rp = self.robots_for(url)
        if not rp:
            return MIN_DELAY_SECONDS
        try:
            d = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*") or MIN_DELAY_SECONDS
            return max(MIN_DELAY_SECONDS, min(float(d), 10.0))
        except Exception:
            return MIN_DELAY_SECONDS


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.text: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False
        self._stack: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        self._stack.append((tag, a))
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "a" and a.get("href"):
            self.links.append((a.get("href", ""), a.get("aria-label") or a.get("title") or ""))
        if tag.lower() == "img":
            src = a.get("src") or a.get("data-src") or a.get("data-original") or ""
            if not src and a.get("srcset"):
                src = a["srcset"].split(",")[-1].strip().split(" ")[0]
            if src:
                parent_bits = []
                for _, pa in self._stack[-4:-1]:
                    parent_bits.extend([pa.get("aria-label", ""), pa.get("title", ""), pa.get("class", "")])
                self.images.append({
                    "src": src,
                    "alt": a.get("alt", ""),
                    "title": a.get("title", ""),
                    "context": " ".join(x for x in parent_bits if x)[:240],
                })
        if tag.lower() == "meta":
            name = (a.get("property") or a.get("name") or "").lower()
            if name in {"og:title", "twitter:title"} and a.get("content"):
                self.title_parts.append(a["content"])

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.text.append(value)
            if self._in_title:
                self.title_parts.append(value)


def normalize_url(raw: str, base: str) -> str | None:
    try:
        url = urllib.parse.urljoin(base, raw.strip())
        p = urllib.parse.urlsplit(url)
        clean = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
        if not host_allowed(clean):
            return None
        if p.path.lower().endswith(('.pdf', '.zip', '.mp4', '.webm', '.jpg', '.jpeg', '.png', '.svg', '.webp')):
            return None
        return clean
    except Exception:
        return None


def sitemap_urls(fetcher: Fetcher, host: str) -> list[str]:
    seeds: list[str] = []
    for s in fetcher.robots_sitemaps.get(host, []):
        if host_allowed(s):
            seeds.append(s)
    if not seeds:
        seeds = [f"https://{host}/sitemap.xml"]
    found: list[str] = []
    seen_maps: set[str] = set()
    queue = seeds[:3]
    while queue and len(found) < 500:
        sm = queue.pop(0)
        if sm in seen_maps or not fetcher.allowed(sm):
            continue
        seen_maps.add(sm)
        try:
            raw, _ = fetcher.request(sm, max_bytes=3_000_000)
            root = ET.fromstring(raw)
            for loc in root.findall('.//{*}loc'):
                url = (loc.text or '').strip()
                if not url:
                    continue
                if url.lower().endswith('.xml') and url not in seen_maps and len(queue) < 3:
                    if host_allowed(url):
                        queue.append(url)
                elif host_allowed(url):
                    found.append(url)
                    if len(found) >= 500:
                        break
        except Exception:
            continue
    return found


def looks_relevant(url: str, label: str = "") -> bool:
    v = (url + " " + label).lower()
    return any(x in v for x in ("badge", "arcade", "catalog", "course", "focus", "skills"))


def image_title(img: dict[str, str], page_title: str) -> str:
    choices = [img.get("alt", ""), img.get("title", "")]
    for choice in choices:
        value = re.sub(r"\s+", " ", choice).strip()
        value = TITLE_NOISE.sub(" ", value)
        value = re.sub(r"\s+", " ", value).strip(" -|:•")
        if 5 <= len(value) <= 140 and not value.lower() in {"badge", "skills", "arcade"}:
            return value
    ctx = re.sub(r"\s+", " ", img.get("context", "")).strip()
    ctx = TITLE_NOISE.sub(" ", ctx)
    ctx = re.sub(r"\s+", " ", ctx).strip(" -|:•")
    if 5 <= len(ctx) <= 140:
        return ctx
    return page_title[:140].strip()


def ocr_image(data: bytes) -> str:
    try:
        from PIL import Image, ImageOps, ImageEnhance
        import pytesseract
        import io
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail((1400, 1400))
        image = ImageOps.grayscale(image)
        image = ImageEnhance.Contrast(image).enhance(1.5)
        return pytesseract.image_to_string(image, config="--psm 6")[:1500]
    except Exception:
        return ""


def classify(evidence: str) -> tuple[str, bool, str, float]:
    """Accept only explicit OCR/context labels; never infer a category from point text."""
    if SKILL_LABEL.search(evidence):
        return "skill", True, "Explicit visible SKILL BADGE label", 1.0
    if GAME_LABEL.search(evidence):
        return "game", True, "Explicit visible Arcade Game/Game Badge label", 1.0
    if COMPLETION_LABEL.search(evidence):
        return "completion", True, "Explicit visible Completion Badge label", 1.0
    return "unknown", False, "No reliable category label found", 0.0


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    previous = load_json(STATE_FILE, {"schema_version": 1, "images": {}, "records": {}})
    known_images: dict[str, Any] = previous.get("images", {}) if isinstance(previous, dict) else {}
    records: dict[str, Any] = previous.get("records", {}) if isinstance(previous, dict) else {}
    started = utc_now()
    fetcher = Fetcher()
    pages: list[str] = []
    page_errors: list[str] = []
    image_discoveries: list[dict[str, str]] = []
    crawled: set[str] = set()

    # Discover public pages through robots.txt sitemaps first, then a small set of seeds.
    queue: list[tuple[str, int]] = [(x, 0) for x in SEEDS]
    for host in sorted(ALLOWED_PAGE_HOSTS):
        maps = sitemap_urls(fetcher, host)
        priority = [u for u in maps if looks_relevant(u)]
        queue.extend((u, 1) for u in priority[:MAX_PAGES])

    while queue and len(crawled) < MAX_PAGES:
        url, depth = queue.pop(0)
        if url in crawled or not fetcher.allowed(url):
            continue
        crawled.add(url)
        try:
            body, mime = fetcher.request(url)
            if mime and "html" not in mime and "xml" not in mime:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(body, "html.parser")
            jsonld = " ".join((x.string or x.get_text(" ", strip=True)) for x in soup.find_all("script", attrs={"type": "application/ld+json"}))
            for hidden in soup(["script", "style", "noscript", "svg"]):
                hidden.decompose()
            title_node = soup.find("title")
            title = (title_node.get_text(" ", strip=True) if title_node else "")[:180] or urllib.parse.urlsplit(url).path
            page_text = (soup.get_text(" ", strip=True) + " " + jsonld)[:10_000]
            if BADGE_WORDS.search(page_text) or BADGE_WORDS.search(url) or "arcade" in title.lower():
                pages.append(url)
                for node in soup.find_all("img"):
                    src = node.get("src") or node.get("data-src") or node.get("data-original") or ""
                    if not src and node.get("srcset"):
                        src = str(node.get("srcset")).split(",")[-1].strip().split(" ")[0]
                    if not src:
                        continue
                    parent_text = node.parent.get_text(" ", strip=True) if node.parent else ""
                    grand_text = node.parent.parent.get_text(" ", strip=True) if node.parent and node.parent.parent else ""
                    im = {
                        "src": str(src),
                        "alt": str(node.get("alt") or ""),
                        "title": str(node.get("title") or ""),
                        "context": (parent_text + " " + grand_text)[:420],
                    }
                    # Preserve the image query string (some public CDN image URLs need it).
                    candidate = urllib.parse.urljoin(url, im["src"])
                    parts = urllib.parse.urlsplit(candidate)
                    im_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, "")) if host_allowed(candidate, image=True) else None
                    parent_link = node.find_parent("a", href=True)
                    badge_url = normalize_url(str(parent_link.get("href")), url) if parent_link else None
                    if im_url and (BADGE_WORDS.search(" ".join(im.values())) or BADGE_WORDS.search(page_text)):
                        image_discoveries.append({
                            "url": im_url,
                            "title": image_title(im, title),
                            "page_url": url,
                            "badge_url": badge_url or url,
                            "context": (im.get("alt", "") + " " + im.get("title", "") + " " + im.get("context", ""))[:700],
                        })
            if depth < 2:
                for node in soup.find_all("a", href=True):
                    label = node.get_text(" ", strip=True) or str(node.get("aria-label") or node.get("title") or "")
                    next_url = normalize_url(str(node.get("href")), url)
                    if next_url and next_url not in crawled and looks_relevant(next_url, label):
                        queue.append((next_url, depth + 1))
        except Exception as exc:
            page_errors.append(f"{url}: {type(exc).__name__}")

    # De-duplicate URLs, then inspect only a capped number of images per scheduled run.
    dedup: dict[str, dict[str, str]] = {}
    for im in image_discoveries:
        dedup.setdefault(im["url"], im)
    image_discoveries = list(dedup.values())[:MAX_IMAGES]
    new_count = 0
    image_errors: list[str] = []

    for im in image_discoveries:
        url = im["url"]
        if not host_allowed(url, image=True) or not fetcher.allowed(url, image=True):
            continue
        try:
            data, mime = fetcher.request(url, max_bytes=MAX_IMAGE_BYTES, image=True)
            if not (mime or "").startswith("image/"):
                raise ValueError("not an image response")
            digest = hashlib.sha256(data).hexdigest()
            old = known_images.get(digest)
            if old:
                # Reuse the prior OCR/classification result without re-running OCR.
                old["last_seen"] = started
                if im["title"] and len(im["title"]) > len(old.get("title", "")):
                    old["title"] = im["title"]
                old["source_url"] = im["page_url"]
                old["badge_url"] = im.get("badge_url") or im["page_url"]
                old["image_url"] = url
                records[digest] = old
                continue

            new_count += 1
            text = ocr_image(data)
            evidence = " ".join([im["context"], text])[:2500]
            # This free-first edition uses OCR and explicit public labels only.
            # No paid model, API key, account credential, or generative-AI request is required.
            ai = None
            cat, verified, why, confidence = classify(evidence)
            record = {
                "title": im["title"] or "Untitled badge candidate",
                "category": cat,
                "verified": verified,
                "status": "active" if verified else "candidate",
                "confidence": round(confidence, 3),
                "evidence": (why + (" — " + re.sub(r"\s+", " ", text).strip()[:180] if text.strip() else ""))[:300],
                "ai_suggestion": None,
                "image_sha256": digest,
                "image_url": url,
                "source_url": im["page_url"],
                "badge_url": im.get("badge_url") or im["page_url"],
                "first_seen": started,
                "last_seen": started,
            }
            known_images[digest] = record
            records[digest] = record
        except Exception as exc:
            image_errors.append(f"{url}: {type(exc).__name__}")

    # Build conservative, clearly separated catalogue groups.
    # Unverified records (including category="unknown") are retained as review candidates;
    # explicit completion badges are kept separate from Skill Badges and Arcade game badges.
    active_records = list(records.values())
    verified_skill: dict[str, dict[str, Any]] = {}
    verified_game: dict[str, dict[str, Any]] = {}
    verified_completion: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for rec in active_records:
        title = str(rec.get("title", "")).strip()
        if len(title) < 5 or title.lower() in {"skills", "arcade", "badge", "untitled badge candidate"}:
            if not rec.get("verified"):
                candidates.append(rec)
            continue
        k = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if rec.get("verified") and rec.get("category") == "skill":
            verified_skill.setdefault(k, rec)
        elif rec.get("verified") and rec.get("category") == "game":
            verified_game.setdefault(k, rec)
        elif rec.get("verified") and rec.get("category") == "completion":
            verified_completion.setdefault(k, rec)
        else:
            candidates.append(rec)

    skill_entries = []
    for rec in sorted(verified_skill.values(), key=lambda x: x["title"].lower()):
        skill_entries.append({
            "title": rec["title"], "aliases": [], "status": "active",
            "category": "skill", "verified": True,
            "evidence": rec.get("evidence", ""), "source_url": rec.get("source_url", ""),
            "badge_url": rec.get("badge_url", rec.get("source_url", "")),
            "image_url": rec.get("image_url", ""),
            "first_seen": rec.get("first_seen", started), "last_seen": rec.get("last_seen", started),
        })

    game_entries = []
    for rec in sorted(verified_game.values(), key=lambda x: x["title"].lower()):
        game_entries.append({
            "title": rec["title"], "category": "game", "verified": True,
            "evidence": rec.get("evidence", ""), "source_url": rec.get("source_url", ""),
            "badge_url": rec.get("badge_url", rec.get("source_url", "")),
            "image_url": rec.get("image_url", ""),
            "first_seen": rec.get("first_seen", started), "last_seen": rec.get("last_seen", started),
        })
    game_keywords = sorted({x["title"] for x in game_entries})

    completion_badges = []
    for rec in sorted(verified_completion.values(), key=lambda x: x["title"].lower()):
        completion_badges.append({
            "title": rec["title"], "category": "completion", "verified": True,
            "evidence": rec.get("evidence", ""), "source_url": rec.get("source_url", ""),
            "badge_url": rec.get("badge_url", rec.get("source_url", "")),
            "image_url": rec.get("image_url", ""),
        })

    # Keep output capped; historical image fingerprints prevent repeated expensive scans.
    if len(known_images) > 2500:
        known_images = dict(list(known_images.items())[-2500:])
    if len(records) > 2500:
        records = dict(list(records.items())[-2500:])
    status = "ok" if pages else ("blocked" if fetcher.blocked_hosts else "no_public_pages_found")
    catalogue = {
        "schema_version": 1,
        "agent": "Arcade Intelligence",
        "version": "1.1.0",
        "status": status,
        "last_run_at": started,
        "entries": skill_entries,
        "game_keywords": game_keywords,
        "game_entries": game_entries,
        "completion_badges": completion_badges,
        "candidates": [
            {k: rec.get(k) for k in ("title", "category", "confidence", "evidence", "source_url", "badge_url", "image_url", "last_seen", "ai_suggestion")}
            for rec in candidates[-400:]
        ],
        "stats": {
            "pages_crawled": len(crawled),
            "pages_with_badge_signals": len(pages),
            "images_discovered": len(dedup),
            "images_selected": len(image_discoveries),
            "new_images_analyzed": new_count,
            "verified_skill_badges": len(skill_entries),
            "verified_game_badges": len(game_entries),
            "verified_completion_badges": len(completion_badges),
            "pending_candidates": len(candidates),
            "ai_api_calls": 0,
            "external_ai_enabled": False,
            "page_errors": len(page_errors),
            "image_errors": len(image_errors),
        },
        "notice": "Free-first OCR discovery only: no generative-AI or vision-model API is called. Only public, robots-allowed Google Skills pages/images on the allowlist are checked. Skill Badges, Arcade game badges, completion badges and unresolved candidates are kept separate. Special +2 points are never inferred; this feed contains catalogue evidence, not point awards. No user profiles are crawled.",
    }
    state = {"schema_version": 1, "updated_at": started, "images": known_images, "records": records}
    last_run = {
        "started_at": started,
        "finished_at": utc_now(),
        "status": status,
        "stats": catalogue["stats"],
        "page_errors": page_errors[:20],
        "image_errors": image_errors[:20],
    }
    save_json(STATE_FILE, state)
    save_json(CATALOGUE_FILE, catalogue)
    save_json(LAST_RUN_FILE, last_run)
    print(json.dumps({"status": status, "stats": catalogue["stats"], "page_errors": page_errors[:3], "image_errors": image_errors[:3]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
