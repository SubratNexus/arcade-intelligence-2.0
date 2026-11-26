#!/usr/bin/env python3
"""Conservative, deterministic classifier for Google Skills Arcade programme events.

This module intentionally avoids guessing. Unknown or weakly matched material is
left unclassified for review rather than being promoted to a confirmed event.
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
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1
        elif tag.lower() in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag.lower() in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
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


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("swag_drop", re.compile(r"\b(swag\s+drop|arcade\s+(?:t-?shirt|techpack|backpack|tumbler|mug|bottle|jacket|fill\s+light)|prize\s+counter\s+(?:update|item|availability))\b", re.I)),
    ("bonus_event", re.compile(r"\b(bonus\s+(?:arcade\s+)?points?|bonus\s+milestone|build[- ]a[- ]thon|extra\s+arcade\s+points?)\b", re.I)),
    ("points_policy", re.compile(r"\b(points?\s+(?:calculation|rule|value|system)|arcade\s+points?\s+(?:change|update|clarification)|points?\s+per\s+game)\b", re.I)),
    ("tier_policy", re.compile(r"\b(tier\s+(?:requirement|threshold|change|update|rule)|trooper|ranger|champion|legend)\b", re.I)),
    ("game_announcement", re.compile(r"\b(new\s+arcade\s+games?|arcade\s+(?:adventure|voyage|trail)|games?\s+(?:launch|released|added|available)|new\s+games?)\b", re.I)),
    ("program_update", re.compile(r"\b((?:new\s+arcade\s+address|arcade\s+has\s+a\s+new\s+address|new\s+address\s+for\s+the\s+arcade)|arcade\s+(?:programme|program)\s+(?:update|rule|deadline)|programme\s+(?:update|rule|deadline)|program\s+(?:update|rule|deadline)|registration\s+(?:open|close)|season\s+(?:end|start)|arcade\s+(?:schedule|deadline)|moving\s+the\s+arcade)\b", re.I)),
)


def classify_event(title: str, body: str = "") -> Optional[str]:
    """Return an event category or None. Title is deliberately weighted first."""
    title_text = re.sub(r"\s+", " ", title or "").strip()
    body_text = re.sub(r"\s+", " ", body or "").strip()
    combined = f"{title_text} {body_text[:8000]}"
    title_lower = title_text.lower()

    # High-precision title rules avoid treating a comment mentioning swag as a drop.
    if re.search(r"\bswag\s+drop\s*:", title_lower):
        return "swag_drop"
    if re.search(r"\barcade\s+build[- ]a[- ]thon\b", title_lower):
        return "bonus_event"
    if re.search(r"\b(?:arcade\s+games?|new\s+games?)\b", title_lower) and re.search(r"\b(?:october|november|december|january|february|march|april|may|june|july|august|september|202[6-9])\b", title_lower):
        return "game_announcement"

    for category, pattern in _RULES:
        if pattern.search(combined):
            return category
    return None


def extract_explicit_point_mentions(text: str) -> list[str]:
    """Return exact short snippets containing explicit point values; do not calculate."""
    clean = re.sub(r"\s+", " ", text or "")
    found: list[str] = []
    pattern = re.compile(r"\b\d{1,3}\s*(?:(?:arcade|bonus)\s*){0,2}points?\b", re.I)
    for match in pattern.finditer(clean):
        phrase = match.group(0).strip()
        if phrase and phrase not in found:
            found.append(phrase)
    return found[:12]


def extract_tier_mentions(text: str) -> list[str]:
    value = (text or "").lower()
    labels = ("Trooper", "Ranger", "Champion", "Legend")
    return [label for label in labels if re.search(rf"\b{label.lower()}\b", value)]
