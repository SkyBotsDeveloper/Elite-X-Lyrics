from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from cachetools import TTLCache

from elite_x_lyrics.config import Settings
from elite_x_lyrics.lyrics_engine import LyricsEngine
from elite_x_lyrics.models import LyricsResult, SearchSession, SongCandidate
from elite_x_lyrics.telegram_api import TelegramAPI, TelegramAPIError
from elite_x_lyrics.utils import short_hash, split_message, truncate_text


LOGGER = logging.getLogger(__name__)


class EliteXLyricsBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.telegram = TelegramAPI(settings.bot_token, timeout=settings.request_timeout)
        self.engine = LyricsEngine(settings)
        self.sessions: TTLCache[str, SearchSession] = TTLCache(
            maxsize=256,
            ttl=settings.search_session_ttl_seconds,
        )
        self.offset: int | None = None
        self.polling_task: asyncio.Task[None] | None = None
        self.me: dict[str, Any] = {}
        self.username: str = ""

    async def start(self) -> None:
        self.me = await self.telegram.get_me()
        self.username = str(self.me.get("username") or "")
        await self.telegram.set_my_commands(
            [
                {"command": "start", "description": "Show how Elite X Lyrics works"},
                {"command": "help", "description": "See usage examples"},
                {"command": "lyrics", "description": "Search lyrics by title or lines"},
                {"command": "credits", "description": "Show creator credits"},
            ]
        )
        if self.settings.use_webhook and self.settings.webhook_endpoint:
            await self.telegram.set_webhook(self.settings.webhook_endpoint, self.settings.webhook_secret)
            LOGGER.info("Webhook enabled at %s", self.settings.webhook_endpoint)
        else:
            await self.telegram.delete_webhook(drop_pending_updates=False)
            self.polling_task = asyncio.create_task(self._poll_updates())
            LOGGER.info("Polling mode enabled")

    async def stop(self) -> None:
        if self.polling_task:
            self.polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.polling_task
        await self.engine.close()
        await self.telegram.close()

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            await self._handle_message(update["message"])
            return
        if "callback_query" in update:
            await self._handle_callback_query(update["callback_query"])
            return
        if "inline_query" in update:
            await self._handle_inline_query(update["inline_query"])
            return

    async def _poll_updates(self) -> None:
        while True:
            try:
                updates = await self.telegram.get_updates(offset=self.offset, timeout=50)
                for update in updates:
                    self.offset = int(update["update_id"]) + 1
                    await self.handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.exception("Polling loop error: %s", exc)
                await asyncio.sleep(3)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        if not text:
            return

        command, args = self._parse_command(text)
        if command:
            await self._handle_command(message, command, args)
            return

        await self._search_and_respond(
            chat_id=int(message["chat"]["id"]),
            query=text,
            reply_to_message_id=int(message["message_id"]),
        )

    async def _handle_command(self, message: dict[str, Any], command: str, args: str) -> None:
        chat_id = int(message["chat"]["id"])
        reply_to_message_id = int(message["message_id"])

        if command in {"start", "help"}:
            await self.telegram.send_message(
                chat_id,
                self._help_text(),
                reply_to_message_id=reply_to_message_id,
                reply_markup=self._intro_keyboard(),
            )
            return

        if command in {"about", "credits"}:
            await self.telegram.send_message(
                chat_id,
                "Elite X Lyrics\nCreated by Siddhartha Abhimanyu and @IflexElite.\nSend any song name or a few lyrics lines and the bot will search across multiple lyric sources.",
                reply_to_message_id=reply_to_message_id,
            )
            return

        if command == "lyrics":
            if not args:
                await self.telegram.send_message(
                    chat_id,
                    "Use /lyrics <song name or lyrics lines>\nExample: /lyrics Sunday song by Aditya",
                    reply_to_message_id=reply_to_message_id,
                )
                return
            await self._search_and_respond(chat_id, args, reply_to_message_id=reply_to_message_id)
            return

        await self.telegram.send_message(
            chat_id,
            "Unknown command.\nUse /start or /help to see how Elite X Lyrics works.",
            reply_to_message_id=reply_to_message_id,
        )

    async def _search_and_respond(self, chat_id: int, query: str, reply_to_message_id: int | None = None) -> None:
        await self.telegram.send_chat_action(chat_id, "typing")
        candidates = await self.engine.search(query, limit=max(self.settings.result_limit, 6))
        if not candidates:
            await self.telegram.send_message(
                chat_id,
                "I couldn't find a reliable lyrics match for that query.\nTry adding the singer name, movie/album name, or send a longer lyric line.",
                reply_to_message_id=reply_to_message_id,
            )
            return

        if self._should_auto_pick(query, candidates):
            result = await self.engine.fetch_lyrics(candidates[0], original_query=query)
            if result:
                await self._send_lyrics(chat_id, result, reply_to_message_id=reply_to_message_id)
                return

        session_id = short_hash(f"{chat_id}:{query}:{len(candidates)}")
        self.sessions[session_id] = SearchSession(query=query, candidates=candidates)
        await self.telegram.send_message(
            chat_id,
            self._selection_text(query, candidates, 0),
            reply_to_message_id=reply_to_message_id,
            reply_markup=self._selection_keyboard(session_id, candidates, 0),
        )

    async def _handle_callback_query(self, callback_query: dict[str, Any]) -> None:
        callback_id = callback_query["id"]
        data = str(callback_query.get("data") or "")
        message = callback_query.get("message") or {}
        chat_id = int(message["chat"]["id"])
        message_id = int(message["message_id"])

        parts = data.split("|")
        action = parts[0] if parts else ""

        if action == "pick" and len(parts) == 3:
            session = self.sessions.get(parts[1])
            if not session:
                await self.telegram.answer_callback_query(callback_id, "This search session expired. Send the query again.", show_alert=True)
                return
            index = int(parts[2])
            if index >= len(session.candidates):
                await self.telegram.answer_callback_query(callback_id, "That result is no longer available.", show_alert=True)
                return
            await self.telegram.answer_callback_query(callback_id, "Fetching full lyrics...")
            result = await self.engine.fetch_lyrics(session.candidates[index], original_query=session.query)
            if not result:
                await self.telegram.send_message(chat_id, "I found the song but couldn't fetch reliable full lyrics for it.")
                return
            await self._send_lyrics(chat_id, result)
            return

        if action == "page" and len(parts) == 3:
            session = self.sessions.get(parts[1])
            if not session:
                await self.telegram.answer_callback_query(callback_id, "This search session expired. Send the query again.", show_alert=True)
                return
            page = int(parts[2])
            await self.telegram.answer_callback_query(callback_id)
            with contextlib.suppress(TelegramAPIError):
                await self.telegram.edit_message_text(
                    chat_id,
                    message_id,
                    self._selection_text(session.query, session.candidates, page),
                    reply_markup=self._selection_keyboard(parts[1], session.candidates, page),
                )
            return

        if action == "close":
            await self.telegram.answer_callback_query(callback_id, "Closed")
            with contextlib.suppress(TelegramAPIError):
                await self.telegram.edit_message_text(chat_id, message_id, "Search closed.")

    async def _handle_inline_query(self, inline_query: dict[str, Any]) -> None:
        inline_query_id = inline_query["id"]
        query = str(inline_query.get("query") or "").strip()

        if not query:
            await self.telegram.answer_inline_query(
                inline_query_id,
                [
                    {
                        "type": "article",
                        "id": "intro",
                        "title": "Search with Elite X Lyrics",
                        "description": "Type a song name or a few lyrics lines.",
                        "input_message_content": {
                            "message_text": self._intro_text(),
                            "disable_web_page_preview": True,
                        },
                    }
                ],
            )
            return

        candidates = await self.engine.search(query, limit=self.settings.inline_result_limit)
        results: list[dict[str, Any]] = []

        async def build_result(candidate: SongCandidate) -> dict[str, Any] | None:
            try:
                lyrics_result = await asyncio.wait_for(
                    self.engine.fetch_lyrics(candidate, original_query=query),
                    timeout=self.settings.search_timeout,
                )
            except Exception:
                lyrics_result = None
            if not lyrics_result:
                return None
            return {
                "type": "article",
                "id": short_hash(f"inline:{candidate.display_name}:{candidate.url}"),
                "title": truncate_text(candidate.title, 64),
                "description": truncate_text(
                    f"{candidate.artist or 'Unknown artist'} | {lyrics_result.language or lyrics_result.source}",
                    128,
                ),
                "input_message_content": {
                    "message_text": self._format_inline_message(lyrics_result),
                    "disable_web_page_preview": True,
                },
            }

        built = await asyncio.gather(*(build_result(candidate) for candidate in candidates), return_exceptions=True)
        for item in built:
            if isinstance(item, dict):
                results.append(item)

        if not results:
            results = [
                {
                    "type": "article",
                    "id": "not-found",
                    "title": "No reliable inline result",
                    "description": "Open Elite X Lyrics in private chat for full search.",
                    "input_message_content": {
                        "message_text": "No reliable inline lyrics result was ready.\nOpen Elite X Lyrics in private chat and send the same query for full fallbacks and song selection buttons.",
                        "disable_web_page_preview": True,
                    },
                }
            ]

        await self.telegram.answer_inline_query(inline_query_id, results)

    def _parse_command(self, text: str) -> tuple[str | None, str]:
        if not text.startswith("/"):
            return None, ""
        first, _, remainder = text.partition(" ")
        command = first[1:]
        if "@" in command:
            command_name, _, username = command.partition("@")
            if self.username and username.lower() != self.username.lower():
                return None, ""
            command = command_name
        return command.lower(), remainder.strip()

    def _should_auto_pick(self, query: str, candidates: list[SongCandidate]) -> bool:
        if len(candidates) == 1:
            return True
        if len(query.split()) > 7 or "\n" in query:
            return False
        top = candidates[0].search_score
        second = candidates[1].search_score
        return top >= self.settings.auto_pick_score and (top - second) >= self.settings.auto_pick_gap

    def _intro_text(self) -> str:
        inline_hint = f"Use inline mode with @{self.username} <query>." if self.username else "Enable inline mode in BotFather to use inline search."
        return (
            "Elite X Lyrics\n"
            "Send a song name or a few lyrics lines and the bot will search across multiple lyric sources.\n"
            "You can search naturally like Sunday song by Aditya or Tu Mere Koi Na by Arijit Singh.\n"
            "Hindi lyrics are returned in Hinglish when the source uses Devanagari.\n"
            "If multiple songs match, you will get pick buttons so you can choose the right song.\n"
            f"{inline_hint}\n"
            "Created by Siddhartha Abhimanyu and @IflexElite."
        )

    def _help_text(self) -> str:
        inline_hint = f"Inline mode: @{self.username} <song or lyrics>" if self.username else "Inline mode can be enabled from BotFather."
        return (
            "Elite X Lyrics\n"
            "Send a song name or a few lines from any song and I will try to find the full lyrics.\n\n"
            "Commands:\n"
            "/start - show bot intro\n"
            "/help - show usage guide\n"
            "/lyrics <query> - search directly\n"
            "/credits - creator credits\n\n"
            "Examples:\n"
            "Tum Hi Ho\n"
            "tum hi ho hum tere bin\n"
            "Sunday song by Aditya\n"
            "Tu Mere Koi Na by Arijit Singh\n\n"
            "Hindi lyrics are returned in Hinglish when the source is in Devanagari.\n"
            "If multiple songs match, I will show buttons so you can choose the correct one.\n"
            f"{inline_hint}\n"
            "Created by Siddhartha Abhimanyu and @IflexElite."
        )

    def _intro_keyboard(self) -> dict[str, Any]:
        return {"inline_keyboard": [[{"text": "Try Inline Search", "switch_inline_query_current_chat": ""}]]}

    def _selection_text(self, query: str, candidates: list[SongCandidate], page: int) -> str:
        page_size = self.settings.search_page_size
        total_pages = max((len(candidates) - 1) // page_size + 1, 1)
        return (
            f"Multiple matches found for:\n{query}\n\n"
            f"Choose the correct song.\n"
            f"Page {page + 1}/{total_pages}\n"
            "Elite X Lyrics | Siddhartha Abhimanyu | @IflexElite"
        )

    def _selection_keyboard(self, session_id: str, candidates: list[SongCandidate], page: int) -> dict[str, Any]:
        page_size = self.settings.search_page_size
        start = page * page_size
        end = min(start + page_size, len(candidates))
        rows: list[list[dict[str, Any]]] = []

        for index in range(start, end):
            label = truncate_text(candidates[index].display_name, 58)
            rows.append([{"text": label, "callback_data": f"pick|{session_id}|{index}"}])

        nav_row: list[dict[str, Any]] = []
        if page > 0:
            nav_row.append({"text": "Previous", "callback_data": f"page|{session_id}|{page - 1}"})
        if end < len(candidates):
            nav_row.append({"text": "Next", "callback_data": f"page|{session_id}|{page + 1}"})
        if nav_row:
            rows.append(nav_row)

        rows.append([{"text": "Close", "callback_data": f"close|{session_id}"}])
        return {"inline_keyboard": rows}

    async def _send_lyrics(self, chat_id: int, result: LyricsResult, reply_to_message_id: int | None = None) -> None:
        heading = result.title
        if result.artist:
            heading = f"{result.title} - {result.artist}"

        meta_lines = [f"Source: {result.source}"]
        if result.language:
            meta_lines.append(f"Language: {result.language}")
        if result.album:
            meta_lines.append(f"Album: {result.album}")
        meta_lines.append("Elite X Lyrics | Siddhartha Abhimanyu | @IflexElite")

        chunks = split_message(result.lyrics, limit=3600)
        for index, chunk in enumerate(chunks):
            if index == 0:
                text = heading + "\n" + "\n".join(meta_lines) + "\n\n" + chunk
            else:
                text = f"Continued ({index + 1}/{len(chunks)})\n\n{chunk}"
            await self.telegram.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id if index == 0 else None,
            )

    def _format_inline_message(self, result: LyricsResult) -> str:
        heading = result.title
        if result.artist:
            heading = f"{result.title} - {result.artist}"
        content = f"{heading}\nSource: {result.source}\n\n{result.lyrics}"
        return truncate_text(content, 3900)
