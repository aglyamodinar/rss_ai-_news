# AI/LLM Daily Digest

Collects AI/LLM-related posts from selected RSS/Atom feeds, filters and ranks them, adds Russian summaries (via OpenRouter), then sends a daily digest to Telegram.

## Files

- `digest.py` - main script
- `bot.py` - on-demand Telegram bot (no cron)
- `.env.example` - environment variable template
- `state.json` - auto-created cache of seen links
- `bot_state.json` - bot offset + summary cache

## Setup

1. Create Telegram bot via `@BotFather`, copy bot token.
2. Get your chat id (for private chat you can use `@userinfobot`).
3. Set env vars in `.env` (recommended):

```env
TELEGRAM_BOT_TOKEN=YOUR_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY
OPENROUTER_MODEL=openai/gpt-4o-mini
```

Or export in shell:

```bash
export TELEGRAM_BOT_TOKEN='YOUR_TOKEN'
export TELEGRAM_CHAT_ID='YOUR_CHAT_ID'
export OPENROUTER_API_KEY='YOUR_OPENROUTER_KEY'
export OPENROUTER_MODEL='openai/gpt-4o-mini'
```

## Run

Dry run (no Telegram send):

```bash
python3 digest.py --dry-run
```

Send digest:

```bash
python3 digest.py
```

Digest with more RU summaries:

```bash
python3 digest.py --ru-summary-limit 8
```

## On-demand Telegram bot (no cron)

Run:

```bash
python3 bot.py
```

Docker run:

```bash
docker compose up -d --build
docker compose logs -f rss-news-bot
```

State in Docker is stored in `./data/bot_state.json` (mounted to `/app/data`).

Commands in Telegram:

```text
/sources
/get <source> [ai|all] [limit]
```

Examples:

```text
/get atlantic ai 5
/get brookings all 7
/get all ai 10
```

Each `/get` response now includes `OpenRouter spend`:
- number of generated/cached summaries
- prompt/completion/total tokens
- cost (`actual` if returned by provider, otherwise `estimated` if rates are set)

Optional pricing env vars for estimation (USD per 1M tokens):

```env
OPENROUTER_PRICE_INPUT_PER_M=0.15
OPENROUTER_PRICE_OUTPUT_PER_M=0.60
```

Optional:

```env
BOT_STATE_FILE=/app/data/bot_state.json
```

Available source ids:

- `atlantic`
- `brookings`
- `jbersin`
- `ies`
- `leadfuture`
- `a16znews`

## Cron (daily at 08:00)

Open crontab:

```bash
crontab -e
```

Add:

```cron
0 8 * * * cd /Users/dinar_mac_mini/Documents/Python\ 2026/RSS\ Новости && /usr/bin/python3 digest.py >> digest.log 2>&1
```

## Deploy on Timeweb (Docker)

On server:

```bash
git clone https://github.com/aglyamodinar/rss-news-bot.git
cd rss-news-bot
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN / OPENROUTER_API_KEY and other vars
docker compose up -d --build
docker compose ps
docker compose logs -f rss-news-bot
```

## Tuning

- Feeds list: edit `FEEDS` in `digest.py`
- Lookback window: `--hours 48`
- Digest size: `--limit 20`
- Russian summaries count: `--ru-summary-limit 5`
- Keywords/excludes: edit `KEYWORDS` and `EXCLUDE_PATTERNS`
