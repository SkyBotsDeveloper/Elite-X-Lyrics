from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SongCandidate:
    title: str
    artist: str = ""
    album: str = ""
    duration_seconds: int | None = None
    year: str = ""
    source: str = ""
    url: str = ""
    search_score: float = 0.0
    language_hint: str = ""
    exact_lyrics: str = ""
    lyrics_preview: str = ""
    provider_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        if self.artist:
            return f"{self.title} - {self.artist}"
        return self.title

    @property
    def dedupe_key(self) -> str:
        artist = self.artist.lower().strip()
        title = self.title.lower().strip()
        return f"{title}|{artist}"


@dataclass(slots=True)
class LyricsResult:
    title: str
    artist: str
    lyrics: str
    source: str
    album: str = ""
    url: str = ""
    language: str = ""
    was_transliterated: bool = False
    notes: str = ""


@dataclass(slots=True)
class SearchSession:
    query: str
    candidates: list[SongCandidate]
