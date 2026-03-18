# Elite X Lyrics

Elite X Lyrics is a Telegram bot that lets users send a song name or a few lines from a song and get back the lyrics.

It is designed for:

- English songs in English
- Hindi songs in Hinglish when the source lyrics are in Devanagari
- Multiple search fallbacks across lyric providers
- Ambiguous song names with button-based selection
- Telegram inline mode result picking
- Deployment on Heroku or a VPS

Creator credits:

- Siddhartha Abhimanyu
- @IflexElite

## Features

- Natural search queries like `Sunday song by Aditya`
- Fuzzy matching for minor spelling mistakes like `Arjit` instead of `Arijit`
- Regular chat flow: send a title or lyric line and pick the correct song if there are multiple matches
- Inline mode flow: users can search with `@YourBotUsername query`
- Multi-source lookup pipeline:
  - YouTube Music
  - LRCLIB
  - Genius
  - Hindi lyric sites like LyricsMint, LyricsGoal, and HindiTracks
- Webhook mode for Heroku
- Polling mode for VPS or simple single-process hosting

## Project Structure

`elite_x_lyrics/` contains the application code:

- `config.py`: environment-driven settings
- `telegram_api.py`: direct Telegram Bot API wrapper
- `lyrics_engine.py`: search and fallback lyrics retrieval logic
- `transliteration.py`: Hindi Devanagari to Hinglish conversion
- `bot.py`: Telegram update handling, callback buttons, inline mode
- `main.py`: FastAPI app and process entrypoint

## Requirements

- Python 3.13 recommended
- A Telegram bot token from BotFather

## Environment Variables

Copy `.env.example` to `.env` and set these values:

- `TELEGRAM_BOT_TOKEN`: required
- `WEBHOOK_URL`: optional public HTTPS URL, required for webhook mode
- `WEBHOOK_SECRET`: optional webhook verification secret
- `HOST`: default `0.0.0.0`
- `PORT`: default `8080`
- `RESULT_LIMIT`: default `10`
- `INLINE_RESULT_LIMIT`: default `5`
- `LOG_LEVEL`: default `INFO`

## Local Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the bot:

```bash
python -m elite_x_lyrics.main
```

Behavior:

- If `WEBHOOK_URL` is set, the app starts in webhook mode
- If `WEBHOOK_URL` is not set, the app starts in polling mode

## Telegram Setup

In BotFather:

1. Create the bot and get the token
2. Set the bot name to `Elite X Lyrics`
3. Set inline mode with `/setinline`
4. Optionally disable privacy mode if you want broader group behavior

## Heroku Deployment

This repo already includes:

- `Procfile`
- `.python-version`
- `app.json`

Steps:

1. Create a Heroku app
2. Add config vars:
   - `TELEGRAM_BOT_TOKEN`
   - `WEBHOOK_URL=https://your-app-name.herokuapp.com`
   - `WEBHOOK_SECRET=your-random-secret`
3. Deploy the repo
4. Start the `web` dyno

The bot will automatically register the Telegram webhook on startup.

## VPS Deployment

Install Python and dependencies:

```bash
pip install -r requirements.txt
```

Run directly:

```bash
python -m elite_x_lyrics.main
```

Or use the sample systemd unit:

- [deploy/systemd/elite-x-lyrics.service](/c:/Users/strad/OneDrive/Documents/shortcuts/Downloads/lyrics%20bot/deploy/systemd/elite-x-lyrics.service)

Adjust the service paths for your server before enabling it.

## User Experience

- Users can send:
  - `Tum Hi Ho`
  - `tum hi ho hum tere bin`
  - `Sunday song by Aditya`
  - `Tu Mere Koi Na by Arijit Singh`
- If there are multiple matches, the bot sends inline buttons with candidate songs
- If lyrics are long, the bot splits them into multiple Telegram messages

## Verification Done

Local verification completed in this workspace:

- Python source compilation with `python -m compileall elite_x_lyrics`
- Search parsing smoke checks for natural queries like `Sunday song by Aditya`
- Fuzzy ranking smoke check for `Arjit` vs `Arijit`
- Bot object initialization smoke check with a placeholder token

Not fully integration-tested here:

- Live Telegram API delivery
- Live external lyrics provider responses
- Real webhook registration against a public HTTPS URL

Those parts depend on real credentials and networked deployment.
