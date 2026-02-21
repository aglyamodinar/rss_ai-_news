#!/usr/bin/env python3
import argparse
import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Set
from urllib.error import HTTPError, URLError

from bot import SOURCES, fetch_source_items
from digest import (
    FeedItem,
    dedupe,
    is_relevant,
    load_dotenv,
    load_state,
    save_state,
    send_telegram,
    summarize_in_russian_openrouter_with_usage,
)


def sort_by_date_desc(items: List[FeedItem]) -> List[FeedItem]:
    return sorted(
        items,
        key=lambda x: x.published.astimezone(timezone.utc) if x.published else datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )


def build_source_message(source_id: str, items: List[FeedItem], now: datetime) -> str:
    source_name = SOURCES[source_id]["name"]
    lines = [f"{now.date()} | {source_name} ({source_id}) | новых AI/LLM: {len(items)}", ""]
    for idx, item in enumerate(items, start=1):
        dt = item.published.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if item.published else "n/a"
        short_summary = textwrap.shorten(item.summary or "", width=180, placeholder="...")
        lines.append(f"{idx}. {item.title}")
        lines.append(f"   {dt}")
        if short_summary:
            lines.append(f"   {short_summary}")
        lines.append(f"   {item.link}")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Daily per-source AI/LLM digest for Telegram.")
    parser.add_argument("--state-file", default="source_digest_state.json")
    parser.add_argument("--hours", type=int, default=240, help="Skip items older than this window when published date is present")
    parser.add_argument("--ru-summary-limit-per-source", type=int, default=3, help="How many new items per source to summarize in Russian")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)

    state_path = Path(args.state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = load_state(state_path)
    seen_links: Set[str] = set(state.get("seen_links", []))
    summary_cache: Dict[str, str] = state.get("summary_cache", {})

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
    if not args.dry_run and (not token or not chat_id):
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.")
        return 2

    sent_count = 0
    all_new_links: Set[str] = set()
    errors: List[str] = []

    for source_id in SOURCES.keys():
        try:
            raw_items = fetch_source_items(source_id)
        except (HTTPError, URLError, TimeoutError, ValueError) as err:
            errors.append(f"{source_id}: {err}")
            continue

        fresh: List[FeedItem] = []
        for item in dedupe(raw_items):
            if item.link in seen_links:
                continue
            if item.published and item.published.astimezone(timezone.utc) < cutoff:
                continue
            if not is_relevant(item):
                continue
            fresh.append(item)

        fresh = sort_by_date_desc(fresh)
        if not fresh:
            continue

        if args.ru_summary_limit_per_source > 0 and openrouter_key:
            for item in fresh[: args.ru_summary_limit_per_source]:
                cached = summary_cache.get(item.link)
                if cached:
                    item.summary = cached
                    continue
                try:
                    ru_text, _usage = summarize_in_russian_openrouter_with_usage(
                        item=item,
                        api_key=openrouter_key,
                        model=openrouter_model,
                    )
                    if ru_text:
                        item.summary = ru_text
                        summary_cache[item.link] = ru_text
                except (HTTPError, URLError, TimeoutError, RuntimeError) as err:
                    errors.append(f"OpenRouter ({source_id}, {item.link}): {err}")

        msg = build_source_message(source_id, fresh, now)
        if args.dry_run:
            print(msg)
            print("")
        else:
            send_telegram(msg, token, chat_id)
        sent_count += 1
        all_new_links.update(item.link for item in fresh)

    if errors:
        warn = "Ошибки источников:\n" + "\n".join(f"- {e}" for e in errors)
        if args.dry_run:
            print(warn)
        elif token and chat_id:
            send_telegram(warn, token, chat_id)

    if sent_count == 0:
        msg = f"{now.date()}: новых AI/LLM публикаций по источникам нет."
        if args.dry_run:
            print(msg)
        else:
            send_telegram(msg, token, chat_id)

    if not args.dry_run:
        new_seen = list(seen_links.union(all_new_links))
        state["seen_links"] = new_seen[-10000:]
        if summary_cache:
            keep_links = set(state["seen_links"][-3000:])
            state["summary_cache"] = {k: v for k, v in summary_cache.items() if k in keep_links}
        save_state(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
