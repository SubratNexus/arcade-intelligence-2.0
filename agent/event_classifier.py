#!/usr/bin/env python3
"""Conservative title-first classifier for Google Skills Arcade programme items.

Only clear programme-specific announcement titles are classified. Body text is
intentionally not used to infer event type because forum replies and quoted
conversation can contain incidental mentions of swag, tiers, points or games.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        parser.close()
        return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    except Exception:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


_PROGRAM_CONTEXT = re.compile(r"\b(?:google\s+skills\s+arcade|skills\s+arcade|arcade)\b", re.I)
_SPECULATION = re.compile(
    r"\b(?:anyone\s+know|anybody\s+know|does\s+anyone|did\s+anyone|"
    r"can\s+anyone\s+confirm|is\s+it\s+true|rumou?r|heard\s+that)\b",
    re.I,
)


def classify_event(title: str, body: str = "") -> Optional[str]:
    """Return a programme event category or None.

    The ``body`` argument remains for API compatibility with the original
    watcher, but classification relies on the title only to avoid promoting
    casual mentions in post text or discussion into event detections.
    """
    title_text = re.sub(r"\s+", " ", title or "").strip()
    if not title_text:
        return None

    # A question/speculation headline is a lead for human review, not an event.
    if _SPECULATION.search(title_text) or "?" in title_text:
        return None

    if not _PROGRAM_CONTEXT.search(title_text):
        return None

    # Order matters: a Build-a-thon / bonus milestone is not a general policy.
    if re.search(r"\bswag\s+drop\b", title_text, re.I):
        return "swag_drop"

    if re.search(r"\bprize\s+counter\b", title_text, re.I) and re.search(
        r"\b(?:update|status|opening|opens?|window|availability|available|items?|redeem|redemption|schedule|"
        r"stock|closing|closes?|closed)\b",
        title_text,
        re.I,
    ):
        return "prize_counter_update"

    if re.search(r"\b(?:build[- ]a[- ]thon|bonus\s+milestone)\b", title_text, re.I):
        return "bonus_event"
    if re.search(r"\bbonus\s+(?:arcade\s+)?points?\b", title_text, re.I) and re.search(
        r"\b(?:event|challenge|campaign|available|launch|drop|week|earn|extra)\b",
        title_text,
        re.I,
    ):
        return "bonus_event"

    if re.search(r"\barcade\s+points?\b", title_text, re.I) and re.search(
        r"\b(?:calculation|calculations|rule|rules|value|values|system|change|changes|changed|update|clarification|per\s+game|earning)\b",
        title_text,
        re.I,
    ):
        return "points_policy"

    has_tier_context = re.search(r"\b(?:tiers?|eligibility)\b", title_text, re.I)
    has_tier_change = re.search(
        r"\b(?:202[6-9]|requirement|requirements|threshold|thresholds|change|changes|update|"
        r"rule|rules|structure|criteria|eligible)\b",
        title_text,
        re.I,
    )
    if has_tier_context and has_tier_change:
        return "tier_policy"

    if re.search(r"\beligibility\b", title_text, re.I) and re.search(
        r"\b(?:change|changes|update|requirement|requirements|criteria|rule|rules)\b",
        title_text,
        re.I,
    ):
        return "tier_policy"

    if re.search(
        r"\b(?:new\s+(?:arcade\s+)?games?|arcade\s+games?\s+(?:for|in|launch|released|added|available)|"
        r"game\s+announcement|new\s+arcade\s+game\s+badges?)\b",
        title_text,
        re.I,
    ):
        return "game_announcement"

    if re.search(r"\binfocus\b", title_text, re.I) and re.search(r"\b(?:week|month)\b", title_text, re.I):
        return "programme_roundup"

    if re.search(
        r"\b(?:new\s+address|address\s+change|deadline|registration|schedule|season\s+(?:start|end)|"
        r"facilitator(?:s)?|re[- ]?trail|special\s+announcement|important\s+announcement|"
        r"programme\s+updates?|program\s+updates?|programme\s+rules?|program\s+rules?|"
        r"arcade\s+rules?\s+(?:change|changes|update|updates))\b",
        title_text,
        re.I,
    ):
        return "program_update"

    return None


def extract_explicit_point_mentions(text: str) -> list[str]:
    """Return exact short snippets containing explicit point values; do not calculate."""
    clean = re.sub(r"\s+", " ", text or "")
    found: list[str] = []
    pattern = re.compile(r"\b\d{1,3}\s*(?:(?:arcade|bonus)\s*){0,2}points?\b", re.I)
    for match in pattern.finditer(clean):
        phrase = match.group(0).strip()
        if phrase and phrase.casefold() not in {item.casefold() for item in found}:
            found.append(phrase)
    return found[:12]


def extract_tier_mentions(text: str) -> list[str]:
    value = text or ""
    labels = ("Trooper", "Ranger", "Champion", "Legend")
    return [label for label in labels if re.search(rf"\b{label}\b", value, re.I)]
