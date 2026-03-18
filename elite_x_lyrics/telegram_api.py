from __future__ import annotations

from typing import Any

import httpx


class TelegramAPIError(RuntimeError):
    """Raised when Telegram returns an API error."""


class TelegramAPI:
    def __init__(self, token: str, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=timeout,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, payload: dict[str, Any] | None = None, http_method: str = "POST") -> Any:
        if http_method.upper() == "GET":
            response = await self._client.get(method, params=payload)
        else:
            response = await self._client.post(method, json=payload or {})
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise TelegramAPIError(f"Telegram API call failed for {method}: {body}")
        return body.get("result")

    async def get_me(self) -> dict[str, Any]:
        return await self.request("getMe", http_method="GET")

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> Any:
        return await self.request("sendChatAction", {"chat_id": chat_id, "action": action})

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> Any:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.request("sendMessage", payload)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> Any:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.request("editMessageText", payload)

    async def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> Any:
        payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        return await self.request("answerCallbackQuery", payload)

    async def answer_inline_query(
        self,
        inline_query_id: str,
        results: list[dict[str, Any]],
        cache_time: int = 5,
        is_personal: bool = True,
    ) -> Any:
        payload = {
            "inline_query_id": inline_query_id,
            "results": results,
            "cache_time": cache_time,
            "is_personal": is_personal,
            "switch_pm_text": "Open Elite X Lyrics",
            "switch_pm_parameter": "inline",
        }
        return await self.request("answerInlineQuery", payload)

    async def set_webhook(self, url: str, secret_token: str | None = None) -> Any:
        payload: dict[str, Any] = {
            "url": url,
            "allowed_updates": ["message", "callback_query", "inline_query"],
            "drop_pending_updates": False,
        }
        if secret_token:
            payload["secret_token"] = secret_token
        return await self.request("setWebhook", payload)

    async def delete_webhook(self, drop_pending_updates: bool = False) -> Any:
        return await self.request("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    async def get_updates(self, offset: int | None = None, timeout: int = 50) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query", "inline_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return await self.request("getUpdates", payload, http_method="GET")
