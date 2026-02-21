#!/usr/bin/env python3
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from digest import (
    FeedItem,
    clean_html,
    estimate_openrouter_cost_usd,
    is_relevant,
    load_dotenv,
    load_state,
    parse_date,
    parse_feed,
    save_state,
    score,
    split_for_telegram,
    summarize_in_russian_openrouter_with_usage,
)


STATE_FILE = Path("bot_state.json")

SOURCES = {
    "atlantic": {
        "name": "The Atlantic",
        "kind": "feed",
        "url": "https://www.theatlantic.com/feed/all/",
    },
    "brookings": {
        "name": "Brookings",
        "kind": "brookings_api",
        "url": "https://www.brookings.edu/wp-json/wp/v2/article?per_page=20",
    },
    "jbersin": {
        "name": "Josh Bersin",
        "kind": "feed",
        "url": "https://joshbersin.com/feed/",
    },
    "ies": {
        "name": "Employment Studies",
        "kind": "html_links",
        "urls": [
            "https://www.employment-studies.co.uk/",
            "https://www.employment-studies.co.uk/news-press/",
            "https://www.employment-studies.co.uk/blogs/",
            "https://www.employment-studies.co.uk/publications/",
        ],
        "include_patterns": [
            r"^https://www\.employment-studies\.co\.uk/resource/",
            r"^https://www\.employment-studies\.co\.uk/newsnblogs/",
        ],
    },
    "leadfuture": {
        "name": "Lead The Future",
        "kind": "feed",
        "url": "https://www.leadthefuture.org/articles?format=rss",
    },
    "a16znews": {
        "name": "a16z news",
        "kind": "feed",
        "url": "https://www.a16z.news/feed",
    },
}


def fetch_text(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": "rss-ai-telegram-bot/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def parse_brookings_items(raw_json: str) -> List[FeedItem]:
    data = json.loads(raw_json)
    out: List[FeedItem] = []
    for row in data:
        title = clean_html((row.get("title") or {}).get("rendered", ""))
        link = row.get("link", "").strip()
        excerpt = clean_html((row.get("excerpt") or {}).get("rendered", ""))
        if not excerpt:
            excerpt = clean_html((row.get("yoast_head_json") or {}).get("description", ""))
        published = parse_date(row.get("date_gmt") or row.get("date"))
        if title and link:
            out.append(
                FeedItem(
                    source="Brookings",
                    title=title,
                    link=link,
                    summary=excerpt,
                    published=published,
                )
            )
    return out


def fetch_source_items(source_id: str) -> List[FeedItem]:
    cfg = SOURCES[source_id]
    if cfg["kind"] == "html_links":
        return fetch_html_link_items(
            source_name=cfg["name"],
            urls=cfg["urls"],
            include_patterns=cfg["include_patterns"],
        )

    raw = fetch_text(cfg["url"])
    if cfg["kind"] == "feed":
        return parse_feed(raw, cfg["name"])
    if cfg["kind"] == "brookings_api":
        return parse_brookings_items(raw)
    raise ValueError(f"Unsupported source kind: {cfg['kind']}")


def fetch_html_link_items(
    source_name: str,
    urls: List[str],
    include_patterns: List[str],
) -> List[FeedItem]:
    link_map: Dict[str, str] = {}
    regexes = [re.compile(p) for p in include_patterns]

    for page_url in urls:
        html = fetch_text(page_url)
        # Capture href and visible text in simple anchors.
        for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.IGNORECASE | re.DOTALL):
            href = href.strip()
            if href.startswith("/"):
                href = "https://www.employment-studies.co.uk" + href
            if not href.startswith("http"):
                continue
            if not any(rgx.search(href) for rgx in regexes):
                continue
            title = clean_html(text)
            if not title or len(title) < 8:
                continue
            if href not in link_map:
                link_map[href] = title

    items: List[FeedItem] = []
    for link, title in link_map.items():
        items.append(
            FeedItem(
                source=source_name,
                title=title,
                link=link,
                summary="",
                published=None,
            )
        )
    return items


def format_sources() -> str:
    lines = ["Доступные источники:"]
    for key, cfg in SOURCES.items():
        lines.append(f"- `{key}` -> {cfg['name']}")
    lines.append("")
    lines.append("Команда: `/get <source> [ai|all] [limit]`")
    lines.append("Пример: `/get atlantic ai 5`")
    lines.append("Пример: `/get brookings all 7`")
    lines.append("Все источники: `/get all ai 10`")
    return "\n".join(lines)


def build_digest_text(
    source_label: str,
    items: List[FeedItem],
    mode: str,
    limit: int,
    openrouter_key: str,
    openrouter_model: str,
    summary_cache: Dict[str, str],
) -> Tuple[str, Dict[str, float]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=10)

    picked: List[FeedItem] = []
    for item in items:
        if item.published and item.published.astimezone(timezone.utc) < cutoff:
            continue
        if mode == "ai" and not is_relevant(item):
            continue
        picked.append(item)

    if mode == "ai":
        picked = sorted(picked, key=lambda x: score(x, now), reverse=True)
    else:
        picked = sorted(
            picked,
            key=lambda x: x.published.astimezone(timezone.utc) if x.published else datetime(1970, 1, 1, tzinfo=timezone.utc),
            reverse=True,
        )
    picked = picked[:limit]

    stats: Dict[str, float] = {
        "generated": 0.0,
        "cached": 0.0,
        "prompt_tokens": 0.0,
        "completion_tokens": 0.0,
        "total_tokens": 0.0,
        "actual_cost_usd": 0.0,
        "estimated_cost_usd": 0.0,
        "actual_cost_count": 0.0,
        "estimated_cost_count": 0.0,
    }

    if not picked:
        return f"{source_label}: ничего не найдено (mode={mode}).", stats

    lines = [f"{source_label}: {len(picked)} материалов (mode={mode})", ""]
    for idx, item in enumerate(picked, start=1):
        dt = item.published.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if item.published else "n/a"
        lines.append(f"{idx}. [{item.source}] {item.title}")
        lines.append(f"   {dt}")

        ru = summary_cache.get(item.link, "")
        if not ru and openrouter_key:
            try:
                ru, usage = summarize_in_russian_openrouter_with_usage(
                    item=item,
                    api_key=openrouter_key,
                    model=openrouter_model,
                )
                if ru:
                    summary_cache[item.link] = ru
                stats["generated"] += 1
                stats["prompt_tokens"] += float(usage.get("prompt_tokens", 0) or 0)
                stats["completion_tokens"] += float(usage.get("completion_tokens", 0) or 0)
                stats["total_tokens"] += float(usage.get("total_tokens", 0) or 0)
                cost = usage.get("cost_usd")
                if cost is not None:
                    try:
                        stats["actual_cost_usd"] += float(cost)
                        stats["actual_cost_count"] += 1
                    except (TypeError, ValueError):
                        pass
                est_cost = usage.get("estimated_cost_usd")
                if est_cost is None:
                    est_cost = estimate_openrouter_cost_usd(usage)
                if est_cost is not None:
                    stats["estimated_cost_usd"] += float(est_cost)
                    stats["estimated_cost_count"] += 1
            except Exception:
                ru = ""
        elif ru:
            stats["cached"] += 1
        if ru:
            lines.append(f"   RU: {ru}")
        elif item.summary:
            lines.append(f"   {item.summary[:220]}{'...' if len(item.summary) > 220 else ''}")

        lines.append(f"   {item.link}")
        lines.append("")
    return "\n".join(lines).strip(), stats


def tg_api(token: str, method: str, params: Optional[dict] = None, timeout: int = 35) -> dict:
    base = f"https://api.telegram.org/bot{token}/{method}"
    if params is None:
        params = {}
    payload = urlencode(params).encode("utf-8")
    req = Request(base, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {raw[:300]}")
    return data


def send_text(token: str, chat_id: str, text: str) -> None:
    for chunk in split_for_telegram(text):
        tg_api(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": "true",
            },
        )


def parse_get_command(text: str) -> Tuple[str, str, int]:
    # /get <source> [ai|all] [limit]
    parts = re.split(r"\s+", text.strip())
    source = "all"
    mode = "ai"
    limit = 5
    if len(parts) >= 2:
        source = parts[1].lower()
    if len(parts) >= 3:
        mode = parts[2].lower()
    if len(parts) >= 4:
        try:
            limit = max(1, min(20, int(parts[3])))
        except ValueError:
            limit = 5
    if mode not in {"ai", "all"}:
        mode = "ai"
    return source, mode, limit


def handle_message(
    text: str,
    state: dict,
    openrouter_key: str,
    openrouter_model: str,
) -> str:
    summary_cache = state.setdefault("summary_cache", {})

    if text.startswith("/start"):
        return (
            "Бот запущен. Отправь /sources для списка источников.\n"
            "Основная команда: /get <source> [ai|all] [limit]"
        )
    if text.startswith("/sources"):
        return format_sources()
    if text.startswith("/get"):
        source, mode, limit = parse_get_command(text)
        source_ids = list(SOURCES.keys()) if source == "all" else [source]
        bad = [s for s in source_ids if s not in SOURCES]
        if bad:
            return f"Неизвестный источник: {', '.join(bad)}\n\n{format_sources()}"

        all_items: List[FeedItem] = []
        errors: List[str] = []
        for sid in source_ids:
            try:
                all_items.extend(fetch_source_items(sid))
            except (HTTPError, URLError, json.JSONDecodeError, TimeoutError, ValueError) as err:
                errors.append(f"{sid}: {err}")

        label = " / ".join(source_ids)
        answer, spend_stats = build_digest_text(
            source_label=label,
            items=all_items,
            mode=mode,
            limit=limit,
            openrouter_key=openrouter_key,
            openrouter_model=openrouter_model,
            summary_cache=summary_cache,
        )
        generated = int(spend_stats.get("generated", 0))
        cached = int(spend_stats.get("cached", 0))
        prompt_tokens = int(spend_stats.get("prompt_tokens", 0))
        completion_tokens = int(spend_stats.get("completion_tokens", 0))
        total_tokens = int(spend_stats.get("total_tokens", 0))
        actual_cost = float(spend_stats.get("actual_cost_usd", 0.0))
        estimated_cost = float(spend_stats.get("estimated_cost_usd", 0.0))
        actual_count = int(spend_stats.get("actual_cost_count", 0))
        estimated_count = int(spend_stats.get("estimated_cost_count", 0))

        if generated > 0 or cached > 0:
            if actual_count > 0:
                cost_text = f"${actual_cost:.6f} (actual)"
            elif estimated_count > 0:
                cost_text = f"${estimated_cost:.6f} (estimated)"
            elif openrouter_key:
                cost_text = "n/a (set OPENROUTER_PRICE_INPUT_PER_M and OPENROUTER_PRICE_OUTPUT_PER_M)"
            else:
                cost_text = "disabled"
            answer += (
                "\n\nOpenRouter spend:\n"
                f"- generated: {generated}, cached: {cached}\n"
                f"- tokens: prompt {prompt_tokens}, completion {completion_tokens}, total {total_tokens}\n"
                f"- cost: {cost_text}"
            )
        if errors:
            answer += "\n\nОшибки источников:\n" + "\n".join(f"- {e}" for e in errors)
        return answer

    return "Команда не распознана. Используй /sources или /get <source> [ai|all] [limit]"


def main() -> int:
    load_dotenv()

    import os

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    default_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()

    if not tg_token:
        print("Set TELEGRAM_BOT_TOKEN in .env")
        return 2

    state = load_state(STATE_FILE)
    state.setdefault("summary_cache", {})
    offset = int(state.get("last_update_id", 0)) + 1

    print("Bot is running. Use Ctrl+C to stop.")
    try:
        while True:
            try:
                data = tg_api(
                    tg_token,
                    "getUpdates",
                    {"timeout": 25, "offset": offset},
                    timeout=35,
                )
                for upd in data.get("result", []):
                    upd_id = int(upd.get("update_id", 0))
                    offset = max(offset, upd_id + 1)
                    msg = upd.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    if not text:
                        continue

                    chat_id = str((msg.get("chat") or {}).get("id") or default_chat_id).strip()
                    if not chat_id:
                        continue

                    reply = handle_message(text, state, openrouter_key, openrouter_model)
                    send_text(tg_token, chat_id, reply)

                    state["last_update_id"] = upd_id
                    # keep cache bounded
                    cache = state.get("summary_cache", {})
                    if isinstance(cache, dict) and len(cache) > 3000:
                        keep = list(cache.items())[-2000:]
                        state["summary_cache"] = {k: v for k, v in keep}
                    save_state(STATE_FILE, state)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as err:
                print(f"poll error: {err}")
                time.sleep(3)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
