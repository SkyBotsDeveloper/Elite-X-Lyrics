from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request

from elite_x_lyrics.bot import EliteXLyricsBot
from elite_x_lyrics.config import get_settings


settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
bot_service = EliteXLyricsBot(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot_service.start()
    try:
        yield
    finally:
        await bot_service.stop()


app = FastAPI(title="Elite X Lyrics", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "Elite X Lyrics", "status": "ok"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "service": "Elite X Lyrics",
        "status": "ok",
        "mode": "webhook" if settings.use_webhook else "polling",
    }


@app.post(settings.webhook_path)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if settings.use_webhook and settings.webhook_secret:
        if x_telegram_bot_api_secret_token != settings.webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update = await request.json()
    await bot_service.handle_update(update)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())
