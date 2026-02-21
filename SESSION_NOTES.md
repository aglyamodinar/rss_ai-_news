# Session Notes (2026-02-11)

## User preferences
- Language: Russian.
- Style: practical, concise.
- Wants on-demand Telegram bot commands instead of cron-based push.
- Wants RU summaries via OpenRouter.
- Wants per-request OpenRouter spend shown in bot replies.

## Project path
- `/Users/dinar_mac_mini/Documents/Python 2026/RSS Новости`

## Current scripts
- `digest.py`: daily digest script (RSS/Atom + filtering + Telegram + OpenRouter summaries + cache).
- `bot.py`: Telegram long-polling bot with source selection commands.

## Bot commands
- `/sources`
- `/get <source> [ai|all] [limit]`
  - examples:
  - `/get atlantic ai 5`
  - `/get brookings all 7`
  - `/get all ai 10`

## Source IDs in bot
- `atlantic` -> `https://www.theatlantic.com/feed/all/`
- `brookings` -> `https://www.brookings.edu/wp-json/wp/v2/article?per_page=20` (API fallback instead of feed)
- `jbersin` -> `https://joshbersin.com/feed/`
- `ies` -> HTML link extraction fallback from:
  - `https://www.employment-studies.co.uk/`
  - `https://www.employment-studies.co.uk/news-press/`
  - `https://www.employment-studies.co.uk/blogs/`
  - `https://www.employment-studies.co.uk/publications/`
- `leadfuture` -> `https://www.leadthefuture.org/articles?format=rss`
- `a16znews` -> `https://www.a16z.news/feed`

## OpenRouter integration
- RU summary generation enabled in both `digest.py` and `bot.py`.
- Caching by article URL:
  - `state.json` for digest
  - `bot_state.json` for bot

## Spend tracking added
- Bot reply includes `OpenRouter spend` block:
  - generated/cached summary count
  - prompt/completion/total tokens
  - cost (`actual` if returned by provider, else `estimated` if env rates are set)
- Optional env for estimate:
  - `OPENROUTER_PRICE_INPUT_PER_M`
  - `OPENROUTER_PRICE_OUTPUT_PER_M`

## Env variables in use
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL` (default: `openai/gpt-4o-mini`)
- optional: `OPENROUTER_BASE_URL`
- optional: `OPENROUTER_PRICE_INPUT_PER_M`
- optional: `OPENROUTER_PRICE_OUTPUT_PER_M`

## Run commands
- Bot:
  - `python3 bot.py`
- Digest:
  - `python3 digest.py`
  - test with fresh state:
  - `python3 digest.py --state-file /tmp/fresh_state.json --hours 72 --limit 5 --ru-summary-limit 5`

## Validation performed in this session
- `python3 -m py_compile digest.py bot.py` passed.
- Functional checks performed for all sources.
- `/get` formatting and OpenRouter spend output verified.

## Known behavior
- If no new items in state, digest can send `Sent 0 items`.
- Brookings feed URLs redirect; API endpoint is used instead.
- Employment Studies RSS is often empty; HTML fallback is used.

---

# Session Notes (2026-02-21)

## Deployment status (Timeweb)
- Server: Timeweb Cloud, `1 vCPU / 1 GB RAM / 15 GB NVMe`.
- Repo on server: `/root/rss_ai-_news`.
- Dockerized services:
  - `rss-news-bot` (long polling bot, always on).
  - `rss-news-digest` (one-shot daily per-source digest runner).
- Docker state storage:
  - bot state: `./data/bot_state.json` (via `BOT_STATE_FILE=/app/data/bot_state.json`)
  - daily digest state: `./data/source_digest_state.json`

## Git milestones
- `0bb13b8` Add Docker deployment for Telegram bot and update docs
- `51fccef` Fix Docker bot state path via BOT_STATE_FILE and data volume
- `08d7297` Add daily per-source AI/LLM digest runner for cron
- `f28b1fb` Add RU summaries to daily per-source digest and Moscow cron example

## Daily schedule
- Cron configured on server (`root`) with Moscow timezone:
  - `CRON_TZ=Europe/Moscow`
  - `0 9 * * * cd /root/rss_ai-_news && /usr/bin/docker compose run --rm rss-news-digest python -u daily_source_digest.py --state-file /app/data/source_digest_state.json --ru-summary-limit-per-source 5 >> /root/rss_ai-_news/digest.log 2>&1`

## Recovery / start from current position
- On server:
  - `cd /root/rss_ai-_news && git pull`
  - `docker compose up -d --build rss-news-bot`
  - `docker compose ps`
  - `crontab -l`
- Manual daily digest test:
  - `docker compose run --rm rss-news-digest python -u daily_source_digest.py --state-file /app/data/source_digest_state.json --ru-summary-limit-per-source 5 --dry-run`
- Live bot logs:
  - `docker compose logs -f rss-news-bot`
- Daily digest logs:
  - `tail -n 200 /root/rss_ai-_news/digest.log`

## Important ops note
- If Telegram shows repeated `HTTP 409: Conflict`, another polling client is active with the same bot token. Stop duplicate clients or rotate token in `@BotFather`.
