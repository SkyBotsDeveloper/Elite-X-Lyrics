from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import urlparse

from rapidfuzz import fuzz

from elite_x_lyrics.models import SongCandidate


DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
WHITESPACE_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

LYRICS_NOISE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^you might also like$",
        r"^embed$",
        r"^\d*embed$",
        r"^translations?$",
        r"^read more:?$",
        r"^also check out:?$",
        r"^image credits?:?.*$",
        r"^song details:?$",
        r"^credits:?$",
        r"^singers?:?.*$",
        r"^lyrics by:?.*$",
        r"^music by:?.*$",
        r"^label:?.*$",
        r"^album:?.*$",
        r"^movie:?.*$",
    ]
]


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.lower()
    value = NON_ALNUM_RE.sub(" ", value)
    return WHITESPACE_RE.sub(" ", value).strip()


def short_hash(value: str, length: int = 12) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def contains_devanagari(value: str) -> bool:
    return bool(DEVANAGARI_RE.search(value or ""))


def clean_lyrics_text(value: str) -> str:
    text = html.unescape(value or "")
    text = text.replace("\r", "")
    text = text.replace("\xa0", " ")
    text = text.replace("\u200b", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = WHITESPACE_RE.sub(" ", raw_line).strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            continue
        if any(pattern.match(line) for pattern in LYRICS_NOISE_PATTERNS):
            continue
        cleaned_lines.append(line)

    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()

    return "\n".join(cleaned_lines).strip()


def looks_like_lyrics(value: str) -> bool:
    text = clean_lyrics_text(value)
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        return False
    average = sum(len(line) for line in lines) / max(len(lines), 1)
    if average > 120:
        return False

    navigation_hits = 0
    for line in lines[:10]:
        lowered = line.lower()
        if any(token in lowered for token in ("menu", "search", "subscribe", "copyright", "advertisement")):
            navigation_hits += 1
    return navigation_hits < 3


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)].rstrip() + "..."


def split_message(text: str, limit: int = 3900) -> list[str]:
    text = text.strip()
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
            continue
        if current:
            chunks.append(current.rstrip())
            current = ""
        if len(line) <= limit:
            current = line
            continue
        start = 0
        while start < len(line):
            piece = line[start : start + limit]
            chunks.append(piece.rstrip())
            start += limit
    if current:
        chunks.append(current.rstrip())
    return chunks or [text[:limit]]


def parse_duration_to_seconds(raw: str | int | float | None) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    value = str(raw).strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    parts = value.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def parse_artist_title_query(query: str) -> tuple[str, str]:
    value = WHITESPACE_RE.sub(" ", (query or "").strip())
    if " - " in value:
        left, right = value.split(" - ", 1)
        if left and right:
            return left.strip(), right.strip()
    lowered = value.lower()
    if " by " in lowered:
        parts = re.split(r"\s+by\s+", value, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[1].strip(), parts[0].strip()
    return "", value


def score_candidate(query: str, candidate: SongCandidate) -> float:
    query_norm = normalize_text(query)
    title_norm = normalize_text(candidate.title)
    artist_norm = normalize_text(candidate.artist)
    combined = normalize_text(f"{candidate.title} {candidate.artist}")

    score_title = fuzz.token_set_ratio(query_norm, title_norm)
    score_combined = fuzz.token_set_ratio(query_norm, combined)
    score_artist_title = fuzz.token_sort_ratio(query_norm, normalize_text(f"{candidate.artist} {candidate.title}"))

    bonus = 0.0
    if query_norm == title_norm:
        bonus += 12.0
    if artist_norm and artist_norm in query_norm:
        bonus += 4.0
    if candidate.exact_lyrics:
        bonus += 3.0
    if candidate.url:
        bonus += 2.0

    source_bonus = {
        "ytmusic": 5.0,
        "lrclib": 6.0,
        "genius": 4.0,
    }.get(candidate.provider_payload.get("provider", ""), 0.0)

    return min(100.0, (score_title * 0.45) + (score_combined * 0.4) + (score_artist_title * 0.15) + bonus + source_bonus)


def domain_for_url(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def first_non_empty(*values: str) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""

