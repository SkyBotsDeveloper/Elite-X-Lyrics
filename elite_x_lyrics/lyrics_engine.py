from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from cachetools import TTLCache
from ytmusicapi import YTMusic

from elite_x_lyrics.config import Settings
from elite_x_lyrics.models import LyricsResult, SongCandidate
from elite_x_lyrics.transliteration import to_hinglish
from elite_x_lyrics.utils import (
    clean_lyrics_text,
    contains_devanagari,
    domain_for_url,
    first_non_empty,
    looks_like_lyrics,
    parse_artist_title_query,
    parse_duration_to_seconds,
    score_candidate,
)


LOGGER = logging.getLogger(__name__)

WORDPRESS_SELECTORS = [
    ".entry-content",
    ".post-content",
    ".td-post-content",
    "article .entry-content",
]

SITE_SELECTORS: dict[str, list[str]] = {
    "genius.com": ['[data-lyrics-container="true"]'],
    "songlyrics.com": ["#songLyricsDiv"],
    "lyricsmint.com": WORDPRESS_SELECTORS,
    "lyricsgoal.com": WORDPRESS_SELECTORS,
    "hinditracks.in": WORDPRESS_SELECTORS,
    "hinditracks.co.in": WORDPRESS_SELECTORS,
    "lyricsbell.com": WORDPRESS_SELECTORS,
    "songlyricsinenglish.com": [".entry-content", ".inside-article"],
    "lyricsdex.com": [".lyrics-text.original-lyrics", ".original-content", ".lyrics-display"],
    "azlyrics.com": [],
}

PREFERRED_WEB_DOMAINS = {
    "azlyrics.com",
    "hinditracks.co.in",
    "hinditracks.in",
    "lyricsbell.com",
    "lyricsdex.com",
    "lyricsgoal.com",
    "lyricsmint.com",
    "songlyrics.com",
    "songlyricsinenglish.com",
}

LYRICS_START_MARKERS = (
    "lyrics in english",
    "lyrics (romanized)",
    "lyrics in hinglish",
    "lyrics in hindi",
    "original (romanized)",
    "original lyrics",
)

LYRICS_END_MARKERS = (
    "table of contents",
    "lyrics meaning",
    "meaning in hindi",
    "translation in hindi",
    "translation in telugu",
    "lyrics details",
    "song details",
    "video song on youtube",
    "faq",
    "read more",
)


class LyricsEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/133.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        self.search_cache: TTLCache[str, list[SongCandidate]] = TTLCache(maxsize=512, ttl=900)
        self.lyrics_cache: TTLCache[str, LyricsResult] = TTLCache(maxsize=512, ttl=1800)
        self.ytmusic = YTMusic()

    async def close(self) -> None:
        await self.http.aclose()

    async def search(self, query: str, limit: int | None = None) -> list[SongCandidate]:
        wanted = limit or self.settings.result_limit
        cache_key = query.strip().lower()
        if cache_key in self.search_cache:
            return self.search_cache[cache_key][:wanted]

        tasks = [
            self._search_lrclib(query, wanted),
            self._search_genius(query, wanted),
            self._search_ytmusic(query, wanted),
            self._search_duckduckgo(query, min(wanted, 6)),
            self._search_wordpress(query, "lyricsmint.com", 4),
            self._search_wordpress(query, "lyricsgoal.com", 4),
            self._search_wordpress(query, "hinditracks.in", 4),
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[SongCandidate] = []
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                LOGGER.debug("Search provider failed: %s", outcome)
                continue
            results.extend(outcome)

        ranked = self._dedupe_and_rank(query, results)
        self.search_cache[cache_key] = ranked
        return ranked[:wanted]

    async def fetch_lyrics(self, candidate: SongCandidate, original_query: str = "") -> LyricsResult | None:
        cache_key = candidate.dedupe_key
        if cache_key in self.lyrics_cache:
            return self.lyrics_cache[cache_key]

        result = await self.fetch_lyrics_direct(candidate)
        if result and self._should_replace_ytmusic_with_web(candidate, result):
            better_web_result = await self._find_better_web_result(candidate, original_query)
            if better_web_result:
                self.lyrics_cache[cache_key] = better_web_result
                return better_web_result
        if result:
            self.lyrics_cache[cache_key] = result
            return result

        fallback_query = original_query.strip() or candidate.display_name
        fallback_candidates = await self.search(fallback_query, limit=6)
        for fallback in fallback_candidates:
            if fallback.dedupe_key == candidate.dedupe_key and fallback.url == candidate.url:
                continue
            result = await self.fetch_lyrics_direct(fallback)
            if result:
                if self._should_replace_ytmusic_with_web(fallback, result):
                    better_web_result = await self._find_better_web_result(fallback, original_query)
                    if better_web_result:
                        self.lyrics_cache[cache_key] = better_web_result
                        return better_web_result
                self.lyrics_cache[cache_key] = result
                return result
        return None

    async def fetch_lyrics_direct(self, candidate: SongCandidate) -> LyricsResult | None:
        provider = str(candidate.provider_payload.get("provider") or "")
        attempts: list[callable[[], Any]] = [lambda: self._build_result_from_embedded_lyrics(candidate)]

        if provider in {"web", "websearch", "genius"}:
            attempts.extend(
                [
                    lambda: self._fetch_from_url(candidate),
                    lambda: self._fetch_from_lrclib(candidate),
                    lambda: self._fetch_from_ytmusic(candidate),
                ]
            )
        elif provider == "lrclib":
            attempts.extend(
                [
                    lambda: self._fetch_from_lrclib(candidate),
                    lambda: self._fetch_from_url(candidate),
                    lambda: self._fetch_from_ytmusic(candidate),
                ]
            )
        else:
            attempts.extend(
                [
                    lambda: self._fetch_from_lrclib(candidate),
                    lambda: self._fetch_from_ytmusic(candidate),
                    lambda: self._fetch_from_url(candidate),
                ]
            )

        for attempt_factory in attempts:
            try:
                result = await attempt_factory()
            except Exception as exc:
                LOGGER.debug("Lyrics fetch failed for %s: %s", candidate.display_name, exc)
                result = None
            if result:
                self.lyrics_cache[candidate.dedupe_key] = result
                return result
        return None

    async def _search_duckduckgo(self, query: str, limit: int) -> list[SongCandidate]:
        search_query = f"{query} lyrics"
        response = await self.http.get("https://html.duckduckgo.com/html/", params={"q": search_query})
        if response.status_code >= 400:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[SongCandidate] = []
        seen_urls: set[str] = set()

        for link in soup.select("a.result__a, .result__title a"):
            href = str(link.get("href") or "").strip()
            resolved_url = self._extract_duckduckgo_target(href)
            if not resolved_url or resolved_url in seen_urls:
                continue
            domain = domain_for_url(resolved_url)
            if domain not in PREFERRED_WEB_DOMAINS:
                continue

            seen_urls.add(resolved_url)
            title_text = clean_lyrics_text(link.get_text(" ", strip=True))
            title, artist = self._guess_title_artist_from_heading(title_text)
            candidate = SongCandidate(
                title=title or title_text,
                artist=artist,
                source=domain,
                url=resolved_url,
                provider_payload={"provider": "websearch", "domain": domain},
            )
            candidate.search_score = score_candidate(query, candidate)
            candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates

    async def _search_ytmusic(self, query: str, limit: int) -> list[SongCandidate]:
        def do_search() -> list[dict[str, Any]]:
            results = self.ytmusic.search(query, filter="songs", limit=limit)
            if len(results) < max(3, limit // 2):
                results.extend(self.ytmusic.search(query, filter="videos", limit=min(limit, 4)))
            return results

        raw_results = await asyncio.to_thread(do_search)
        candidates: list[SongCandidate] = []
        seen_ids: set[str] = set()

        for item in raw_results:
            video_id = str(item.get("videoId") or "")
            if video_id and video_id in seen_ids:
                continue
            if video_id:
                seen_ids.add(video_id)

            title = str(item.get("title") or "").strip()
            if not title:
                continue

            artists = item.get("artists") or item.get("authors") or []
            artist_names = ", ".join(
                entry.get("name", "").strip()
                for entry in artists
                if isinstance(entry, dict) and entry.get("name")
            )
            album_data = item.get("album") or {}
            album = album_data.get("name", "") if isinstance(album_data, dict) else ""
            candidate = SongCandidate(
                title=title,
                artist=artist_names,
                album=album,
                duration_seconds=parse_duration_to_seconds(item.get("duration_seconds") or item.get("duration")),
                source="YouTube Music",
                url=f"https://music.youtube.com/watch?v={video_id}" if video_id else "",
                provider_payload={"provider": "ytmusic", "video_id": video_id},
            )
            candidate.search_score = score_candidate(query, candidate)
            candidates.append(candidate)
        return candidates

    async def _search_lrclib(self, query: str, limit: int) -> list[SongCandidate]:
        artist_hint, title_hint = parse_artist_title_query(query)
        requests: list[dict[str, str]] = []
        if title_hint:
            params = {"track_name": title_hint}
            if artist_hint:
                params["artist_name"] = artist_hint
            requests.append(params)
        if title_hint.lower() != query.strip().lower():
            requests.append({"track_name": query})

        candidates: list[SongCandidate] = []
        seen: set[tuple[str, str]] = set()
        for params in requests:
            response = await self.http.get("https://lrclib.net/api/search", params=params)
            if response.status_code >= 400:
                continue
            payload = response.json()
            if not isinstance(payload, list):
                continue
            for item in payload[:limit]:
                title = first_non_empty(str(item.get("trackName") or ""), title_hint)
                artist = first_non_empty(str(item.get("artistName") or ""), artist_hint)
                key = (title.lower().strip(), artist.lower().strip())
                if not title or key in seen:
                    continue
                seen.add(key)
                plain = str(item.get("plainLyrics") or "").strip()
                synced = self._strip_lrc_timestamps(str(item.get("syncedLyrics") or ""))
                candidate = SongCandidate(
                    title=title,
                    artist=artist,
                    album=str(item.get("albumName") or "").strip(),
                    duration_seconds=parse_duration_to_seconds(item.get("duration")),
                    source="LRCLIB",
                    url=f"https://lrclib.net/api/get?{urlencode({'id': item.get('id')})}" if item.get("id") else "",
                    exact_lyrics=plain or synced,
                    provider_payload={
                        "provider": "lrclib",
                        "id": item.get("id"),
                        "track_name": title,
                        "artist_name": artist,
                        "album_name": str(item.get("albumName") or "").strip(),
                        "duration": item.get("duration"),
                    },
                )
                candidate.search_score = score_candidate(query, candidate)
                candidates.append(candidate)
        return candidates

    async def _search_genius(self, query: str, limit: int) -> list[SongCandidate]:
        response = await self.http.get(
            "https://genius.com/api/search/song",
            params={"q": query, "per_page": limit},
        )
        if response.status_code >= 400:
            return []
        payload = response.json()
        sections = payload.get("response", {}).get("sections", [])
        hits: list[dict[str, Any]] = []
        for section in sections:
            hits.extend(section.get("hits", []))
        candidates: list[SongCandidate] = []
        for hit in hits[:limit]:
            result = hit.get("result") or {}
            title = str(result.get("title") or "").strip()
            if not title:
                continue
            candidate = SongCandidate(
                title=title,
                artist=str(result.get("artist_names") or "").strip(),
                source="Genius",
                url=str(result.get("url") or "").strip(),
                year=str(result.get("release_date_for_display") or "").strip(),
                provider_payload={"provider": "genius"},
            )
            candidate.search_score = score_candidate(query, candidate)
            candidates.append(candidate)
        return candidates

    async def _search_wordpress(self, query: str, domain: str, limit: int) -> list[SongCandidate]:
        response = await self.http.get(f"https://{domain}/", params={"s": query})
        if response.status_code >= 400:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[SongCandidate] = []
        seen_urls: set[str] = set()
        for link in soup.select("h2.entry-title a, h3.entry-title a, article h2 a, article h3 a"):
            href = str(link.get("href") or "").strip()
            heading = clean_lyrics_text(link.get_text(" ", strip=True))
            if not href or href in seen_urls or not heading:
                continue
            seen_urls.add(href)
            title, artist = self._guess_title_artist_from_heading(heading)
            candidate = SongCandidate(
                title=title or heading,
                artist=artist,
                source=domain,
                url=href,
                provider_payload={"provider": "web", "domain": domain},
            )
            candidate.search_score = score_candidate(query, candidate)
            candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates

    async def _build_result_from_embedded_lyrics(self, candidate: SongCandidate) -> LyricsResult | None:
        if not candidate.exact_lyrics:
            return None
        return self._finalize_result(candidate, candidate.exact_lyrics, candidate.source, candidate.url)

    async def _fetch_from_ytmusic(self, candidate: SongCandidate) -> LyricsResult | None:
        video_id = str(candidate.provider_payload.get("video_id") or "")
        if candidate.provider_payload.get("provider") != "ytmusic" and not video_id:
            return None

        def do_fetch() -> dict[str, Any] | None:
            local_video_id = video_id
            if not local_video_id:
                search_query = candidate.display_name
                search_results = self.ytmusic.search(search_query, filter="songs", limit=1)
                if not search_results:
                    return None
                local_video_id = str(search_results[0].get("videoId") or "")
                if not local_video_id:
                    return None
            watch = self.ytmusic.get_watch_playlist(videoId=local_video_id)
            lyrics_browse_id = watch.get("lyrics")
            if not lyrics_browse_id:
                return None
            return self.ytmusic.get_lyrics(lyrics_browse_id)

        payload = await asyncio.to_thread(do_fetch)
        if not payload:
            return None
        lyrics = payload.get("lyrics")
        if isinstance(lyrics, list):
            lyrics = "\n".join(line.get("text", "") for line in lyrics if isinstance(line, dict))
        if not isinstance(lyrics, str) or not lyrics.strip():
            return None
        return self._finalize_result(candidate, lyrics, "YouTube Music", candidate.url)

    async def _fetch_from_lrclib(self, candidate: SongCandidate) -> LyricsResult | None:
        if candidate.provider_payload.get("provider") == "lrclib" and candidate.exact_lyrics:
            return self._finalize_result(candidate, candidate.exact_lyrics, "LRCLIB", candidate.url)

        params: dict[str, Any] = {}
        payload = candidate.provider_payload
        if payload.get("id"):
            params["id"] = payload.get("id")
        else:
            if candidate.title:
                params["track_name"] = candidate.title
            if candidate.artist:
                params["artist_name"] = candidate.artist
            if candidate.album:
                params["album_name"] = candidate.album
            if candidate.duration_seconds:
                params["duration"] = candidate.duration_seconds
        if not params:
            return None

        response = await self.http.get("https://lrclib.net/api/get", params=params)
        if response.status_code >= 400:
            return None
        data = response.json()
        lyrics = str(data.get("plainLyrics") or "").strip()
        if not lyrics:
            lyrics = self._strip_lrc_timestamps(str(data.get("syncedLyrics") or ""))
        if not lyrics:
            return None
        return self._finalize_result(candidate, lyrics, "LRCLIB", candidate.url)

    async def _fetch_from_url(self, candidate: SongCandidate) -> LyricsResult | None:
        if not candidate.url:
            return None
        response = await self.http.get(candidate.url)
        if response.status_code >= 400:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        lyrics = self._extract_lyrics_from_soup(candidate.url, soup)
        if not lyrics:
            return None
        if not candidate.title or not candidate.artist:
            page_title, page_artist = self._extract_title_artist_from_page(candidate.url, soup)
            if not candidate.title:
                candidate.title = page_title
            if not candidate.artist:
                candidate.artist = page_artist
        return self._finalize_result(candidate, lyrics, domain_for_url(candidate.url), candidate.url)

    def _dedupe_and_rank(self, query: str, candidates: list[SongCandidate]) -> list[SongCandidate]:
        deduped: dict[str, SongCandidate] = {}
        for candidate in candidates:
            if not candidate.title:
                continue
            candidate.search_score = score_candidate(query, candidate)
            existing = deduped.get(candidate.dedupe_key)
            if existing is None:
                deduped[candidate.dedupe_key] = candidate
                continue
            self._merge_candidate(existing, candidate)
            existing.search_score = max(existing.search_score, candidate.search_score)
        return sorted(
            deduped.values(),
            key=lambda item: (
                item.search_score,
                bool(item.exact_lyrics),
                bool(item.artist),
                bool(item.url),
            ),
            reverse=True,
        )

    def _merge_candidate(self, target: SongCandidate, source: SongCandidate) -> None:
        if not target.artist and source.artist:
            target.artist = source.artist
        if not target.album and source.album:
            target.album = source.album
        if not target.url and source.url:
            target.url = source.url
        if not target.exact_lyrics and source.exact_lyrics:
            target.exact_lyrics = source.exact_lyrics
        if not target.provider_payload:
            target.provider_payload = dict(source.provider_payload)
        else:
            merged = dict(source.provider_payload)
            merged.update(target.provider_payload)
            target.provider_payload = merged

    def _strip_lrc_timestamps(self, value: str) -> str:
        if not value:
            return ""
        return re.sub(r"\[[0-9:.]+\]", "", value).strip()

    def _extract_duckduckgo_target(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("//duckduckgo.com/l/?"):
            href = "https:" + href
        parsed = urlparse(href)
        if "duckduckgo.com" not in parsed.netloc:
            return href
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target

    def _guess_title_artist_from_heading(self, value: str) -> tuple[str, str]:
        text = re.sub(r"\s+", " ", value).strip()
        text = re.sub(r"\blyrics\b.*$", "", text, flags=re.IGNORECASE).strip(" -|:")
        if " - " in text:
            left, right = text.split(" - ", 1)
            return left.strip(), right.strip()
        if "|" in text:
            left, right = text.split("|", 1)
            return left.strip(), right.strip()
        return text, ""

    def _extract_title_artist_from_page(self, url: str, soup: BeautifulSoup) -> tuple[str, str]:
        meta_title = ""
        meta_artist = ""

        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or script.get_text())
            except Exception:
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                by_artist = item.get("byArtist") or {}
                artist = ""
                if isinstance(by_artist, dict):
                    artist = str(by_artist.get("name") or "").strip()
                if name and not meta_title:
                    meta_title = name
                if artist and not meta_artist:
                    meta_artist = artist

        if not meta_title:
            meta_node = soup.select_one('meta[property="og:title"]')
            if meta_node and meta_node.get("content"):
                raw_title = str(meta_node.get("content"))
            elif soup.title and soup.title.string:
                raw_title = soup.title.string
            else:
                raw_title = ""
            meta_title, guessed_artist = self._guess_title_artist_from_heading(raw_title)
            if not meta_artist:
                meta_artist = guessed_artist

        return meta_title, meta_artist

    def _extract_lyrics_from_soup(self, url: str, soup: BeautifulSoup) -> str:
        domain = domain_for_url(url)

        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or script.get_text())
            except Exception:
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                lyrics_text = item.get("lyrics") or item.get("lyricBody")
                if isinstance(lyrics_text, str) and looks_like_lyrics(lyrics_text):
                    return clean_lyrics_text(lyrics_text)

        if domain == "genius.com":
            blocks = [
                clean_lyrics_text(node.get_text("\n", strip=True))
                for node in soup.select('[data-lyrics-container="true"]')
            ]
            blocks = [block for block in blocks if looks_like_lyrics(block)]
            if blocks:
                return "\n".join(blocks).strip()

        if domain == "azlyrics.com":
            for div in soup.select("div.col-xs-12.col-lg-8.text-center div"):
                if div.get("class"):
                    continue
                block = clean_lyrics_text(div.get_text("\n", strip=True))
                if looks_like_lyrics(block):
                    return block

        selectors = SITE_SELECTORS.get(domain, []) + ["article", "section"]
        best_text = ""
        for selector in selectors:
            for node in soup.select(selector):
                block = self._trim_lyrics_block(clean_lyrics_text(node.get_text("\n", strip=True)))
                if not looks_like_lyrics(block):
                    continue
                if len(block) > len(best_text):
                    best_text = block
        return best_text

    def _trim_lyrics_block(self, text: str) -> str:
        if not text:
            return ""

        lines = [line.strip() for line in clean_lyrics_text(text).splitlines()]
        if not lines:
            return ""

        start_index = 0
        for index, line in enumerate(lines):
            lowered = line.lower()
            if any(marker in lowered for marker in LYRICS_START_MARKERS):
                start_index = index + 1

        working = lines[start_index:] if start_index else lines

        while working and (
            ":" in working[0]
            or len(working[0].split()) > 12
            or any(token in working[0].lower() for token in ("song is sung by", "featuring", "lyricist", "music", "movie", "rating"))
        ):
            working.pop(0)

        while working and working[0].lower() in {"original", "translations", "select translation", "romanized"}:
            working.pop(0)

        kept: list[str] = []
        lyric_like_lines = 0
        for line in working:
            lowered = line.lower()
            if lyric_like_lines >= 8 and any(marker in lowered for marker in LYRICS_END_MARKERS):
                break
            kept.append(line)
            if line and len(line.split()) <= 10:
                lyric_like_lines += 1

        return clean_lyrics_text("\n".join(kept))

    def _should_replace_ytmusic_with_web(self, candidate: SongCandidate, result: LyricsResult) -> bool:
        provider = str(candidate.provider_payload.get("provider") or "")
        if provider != "ytmusic":
            return False
        if result.source != "YouTube Music":
            return False
        return result.language.startswith("Hindi")

    async def _find_better_web_result(self, candidate: SongCandidate, original_query: str) -> LyricsResult | None:
        query = " ".join(part for part in [candidate.title, candidate.artist, original_query] if part).strip()
        web_candidates = await self._search_duckduckgo(query, limit=5)
        for web_candidate in web_candidates:
            if domain_for_url(web_candidate.url) not in PREFERRED_WEB_DOMAINS:
                continue
            result = await self._fetch_from_url(web_candidate)
            if result:
                return result
        return None

    def _finalize_result(self, candidate: SongCandidate, lyrics: str, source: str, url: str) -> LyricsResult | None:
        cleaned = self._trim_lyrics_block(clean_lyrics_text(lyrics))
        if not looks_like_lyrics(cleaned):
            return None

        language = "English/Original"
        was_transliterated = False
        if contains_devanagari(cleaned):
            converted = to_hinglish(cleaned)
            if converted and converted != cleaned:
                cleaned = converted
                language = "Hindi (Hinglish)"
                was_transliterated = True
            else:
                language = "Hindi"

        return LyricsResult(
            title=candidate.title or "Unknown Song",
            artist=candidate.artist,
            album=candidate.album,
            lyrics=cleaned,
            source=source,
            url=url,
            language=language,
            was_transliterated=was_transliterated,
        )
