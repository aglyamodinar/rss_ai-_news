#!/usr/bin/env python3
import argparse
import html
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


@dataclass
class FeedItem:
    source: str
    title: str
    link: str
    summary: str
    published: Optional[datetime]


FEEDS = [
    ("The Atlantic", "https://www.theatlantic.com/feed/all/"),
    ("OpenAI Blog", "https://openai.com/news/rss.xml"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("MIT Technology Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
]

# arXiv API (Atom)
ARXIV_QUERY = {
    "search_query": "cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.LG",
    "start": 0,
    "max_results": 50,
    "sortBy": "submittedDate",
    "sortOrder": "descending",
}
ARXIV_FEED = "https://export.arxiv.org/api/query?" + urlencode(ARXIV_QUERY)

KEYWORDS = [
    "ai",
    "artificial intelligence",
    "llm",
    "large language model",
    "gpt",
    "agent",
    "agents",
    "rag",
    "reasoning",
    "prompt",
    "fine-tuning",
    "finetuning",
    "inference",
    "transformer",
    "multimodal",
    "benchmark",
    "token",
]

KEYWORD_REGEX = [
    re.compile(r"\bartificial intelligence\b", re.IGNORECASE),
    re.compile(r"\bllm(s)?\b", re.IGNORECASE),
    re.compile(r"\blarge language model(s)?\b", re.IGNORECASE),
    re.compile(r"\bgpt(-\d+(\.\d+)?)?\b", re.IGNORECASE),
    re.compile(r"\bai\b", re.IGNORECASE),
    re.compile(r"\bagent(s)?\b", re.IGNORECASE),
    re.compile(r"\brag\b", re.IGNORECASE),
    re.compile(r"\breasoning\b", re.IGNORECASE),
    re.compile(r"\bprompt(s|ing)?\b", re.IGNORECASE),
    re.compile(r"\bfine[- ]?tuning\b", re.IGNORECASE),
    re.compile(r"\binference\b", re.IGNORECASE),
    re.compile(r"\btransformer(s)?\b", re.IGNORECASE),
    re.compile(r"\bmultimodal\b", re.IGNORECASE),
    re.compile(r"\bbenchmark(s)?\b", re.IGNORECASE),
    re.compile(r"\btoken(s)?\b", re.IGNORECASE),
]

EXCLUDE_PATTERNS = [
    r"\bjob(s)?\b",
    r"\bcareer(s)?\b",
    r"\bhiring\b",
]


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def fetch_url(url: str, timeout: int = 25) -> str:
    req = Request(url, headers={"User-Agent": "rss-ai-digest/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def clean_html(text: str) -> str:
    unescaped = html.unescape(text or "")
    no_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", no_tags).strip()


def parse_date(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def first_text(elem: ET.Element, paths: List[str], ns: Dict[str, str]) -> str:
    for path in paths:
        found = elem.find(path, ns)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def parse_feed(xml_text: str, source: str) -> List[FeedItem]:
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }
    root = ET.fromstring(xml_text)
    items: List[FeedItem] = []

    # Atom
    atom_entries = root.findall("./atom:entry", ns)
    if atom_entries:
        for entry in atom_entries:
            title = clean_html(first_text(entry, ["atom:title"], ns))
            link = ""
            for link_elem in entry.findall("atom:link", ns):
                href = link_elem.get("href", "")
                rel = link_elem.get("rel", "alternate")
                if href and rel == "alternate":
                    link = href
                    break
                if href and not link:
                    link = href
            summary = first_text(entry, ["atom:summary", "atom:content"], ns)
            published_text = first_text(
                entry, ["atom:published", "atom:updated"], ns
            )
            if title and link:
                items.append(
                    FeedItem(
                        source=source,
                        title=title,
                        link=link,
                        summary=clean_html(summary),
                        published=parse_date(published_text),
                    )
                )
        return items

    # RSS
    rss_items = root.findall(".//item")
    for item in rss_items:
        title = clean_html(first_text(item, ["title"], ns))
        link = first_text(item, ["link"], ns)
        summary = first_text(item, ["description", "content:encoded"], ns)
        published_text = first_text(item, ["pubDate"], ns)
        if title and link:
            items.append(
                FeedItem(
                    source=source,
                    title=title,
                    link=link,
                    summary=clean_html(summary),
                    published=parse_date(published_text),
                )
            )
    return items


def is_relevant(item: FeedItem) -> bool:
    blob = f"{item.title}\n{item.summary}".lower()
    if any(re.search(pattern, blob) for pattern in EXCLUDE_PATTERNS):
        return False
    return any(pattern.search(blob) for pattern in KEYWORD_REGEX)


def score(item: FeedItem, now: datetime) -> int:
    blob = f"{item.title}\n{item.summary}".lower()
    keyword_hits = sum(1 for pattern in KEYWORD_REGEX if pattern.search(blob))
    source_bonus = 2 if item.source in {"OpenAI Blog", "arXiv"} else 1
    freshness_bonus = 0
    if item.published:
        age_hours = (now - item.published.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours <= 24:
            freshness_bonus = 3
        elif age_hours <= 72:
            freshness_bonus = 1
    return keyword_hits * 2 + source_bonus + freshness_bonus


def dedupe(items: List[FeedItem]) -> List[FeedItem]:
    seen = set()
    out = []
    for item in items:
        key = (item.link.strip().lower(), item.title.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_links": [], "summary_cache": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "seen_links" not in data or not isinstance(data.get("seen_links"), list):
            data["seen_links"] = []
        if "summary_cache" not in data or not isinstance(data.get("summary_cache"), dict):
            data["summary_cache"] = {}
        return data
    except json.JSONDecodeError:
        return {"seen_links": [], "summary_cache": {}}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def build_message(
    items: List[FeedItem],
    now: datetime,
    limit: int,
    ru_summaries: Optional[Dict[str, str]] = None,
) -> str:
    picked = items[:limit]
    if not picked:
        return f"AI/LLM digest for {now.date()}: no new relevant items."

    lines = [f"AI/LLM digest for {now.date()} ({len(picked)} items):", ""]
    for idx, item in enumerate(picked, start=1):
        dt = item.published.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if item.published else "n/a"
        short_summary = textwrap.shorten(item.summary or "", width=140, placeholder="...")
        lines.append(f"{idx}. [{item.source}] {item.title}")
        lines.append(f"   {dt}")
        if short_summary:
            lines.append(f"   {short_summary}")
        if ru_summaries and ru_summaries.get(item.link):
            lines.append(f"   RU: {ru_summaries[item.link]}")
        lines.append(f"   {item.link}")
        lines.append("")
    return "\n".join(lines).strip()


def split_for_telegram(text: str, chunk_size: int = 3500) -> List[str]:
    if len(text) <= chunk_size:
        return [text]
    parts: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > chunk_size and current:
            parts.append("".join(current).strip())
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        parts.append("".join(current).strip())
    return parts


def send_telegram(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = split_for_telegram(text)
    for chunk in chunks:
        payload = urlencode(
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if '"ok":true' not in body:
                raise RuntimeError(f"Telegram API response: {body[:300]}")


def summarize_in_russian_openrouter(
    item: FeedItem,
    api_key: str,
    model: str,
    timeout: int = 35,
) -> str:
    text, _ = summarize_in_russian_openrouter_with_usage(
        item=item,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
    return text


def estimate_openrouter_cost_usd(usage: Dict[str, Any]) -> Optional[float]:
    try:
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None
    try:
        in_per_m = float(os.getenv("OPENROUTER_PRICE_INPUT_PER_M", "").strip())
        out_per_m = float(os.getenv("OPENROUTER_PRICE_OUTPUT_PER_M", "").strip())
    except ValueError:
        return None
    if in_per_m < 0 or out_per_m < 0:
        return None
    return (prompt_tokens / 1_000_000.0) * in_per_m + (completion_tokens / 1_000_000.0) * out_per_m


def summarize_in_russian_openrouter_with_usage(
    item: FeedItem,
    api_key: str,
    model: str,
    timeout: int = 35,
) -> Tuple[str, Dict[str, Any]]:
    url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    prompt = (
        "Сделай краткое саммари на русском (2 предложения, максимум 45 слов). "
        "Только факты из текста, без домыслов и оценок. "
        "Если данных мало, так и напиши очень кратко.\n\n"
        f"Источник: {item.source}\n"
        f"Заголовок: {item.title}\n"
        f"Текст: {item.summary}\n"
        f"Ссылка: {item.link}"
    )
    payload_obj = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 120,
        "messages": [
            {"role": "system", "content": "Ты редактор новостного дайджеста."},
            {"role": "user", "content": prompt},
        ],
    }
    payload = json.dumps(payload_obj).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("HTTP-Referer", "https://local-rss-digest")
    req.add_header("X-Title", "AI LLM RSS Digest")
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(body)
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage_out: Dict[str, Any] = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        # Some providers may return explicit cost fields.
        for key in ("cost", "total_cost", "cost_usd"):
            if key in data:
                usage_out["cost_usd"] = data.get(key)
                break
            if key in usage:
                usage_out["cost_usd"] = usage.get(key)
                break
        est = estimate_openrouter_cost_usd(usage_out)
        if est is not None:
            usage_out["estimated_cost_usd"] = est
        return re.sub(r"\s+", " ", text).strip(), usage_out
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as err:
        raise RuntimeError(f"OpenRouter bad response: {err}; body={body[:260]}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Daily AI/LLM RSS digest.")
    parser.add_argument("--state-file", default="state.json")
    parser.add_argument("--hours", type=int, default=36, help="Look back window")
    parser.add_argument("--limit", type=int, default=15, help="Max items in digest")
    parser.add_argument("--ru-summary-limit", type=int, default=5, help="How many items to summarize in Russian")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)

    all_feeds = FEEDS + [("arXiv", ARXIV_FEED)]
    collected: List[FeedItem] = []
    errors: List[str] = []

    for source, url in all_feeds:
        try:
            xml_text = fetch_url(url)
            collected.extend(parse_feed(xml_text, source))
        except (HTTPError, URLError, ET.ParseError, TimeoutError, ValueError) as err:
            errors.append(f"{source}: {err}")

    if not collected and errors:
        print("All feeds failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    state_path = Path(args.state_file)
    state = load_state(state_path)
    seen_links = set(state.get("seen_links", []))

    fresh = []
    for item in dedupe(collected):
        if item.link in seen_links:
            continue
        if item.published and item.published.astimezone(timezone.utc) < cutoff:
            continue
        if is_relevant(item):
            fresh.append(item)

    ranked = sorted(fresh, key=lambda x: score(x, now), reverse=True)

    ru_summaries: Dict[str, str] = {}
    summary_cache: Dict[str, str] = state.get("summary_cache", {})
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()

    if args.ru_summary_limit > 0 and openrouter_key:
        for item in ranked[: args.limit][: args.ru_summary_limit]:
            cached = summary_cache.get(item.link)
            if cached:
                ru_summaries[item.link] = cached
                continue
            try:
                summary_ru = summarize_in_russian_openrouter(
                    item=item,
                    api_key=openrouter_key,
                    model=openrouter_model,
                )
                if summary_ru:
                    ru_summaries[item.link] = summary_ru
                    summary_cache[item.link] = summary_ru
            except (HTTPError, URLError, TimeoutError, RuntimeError) as err:
                errors.append(f"OpenRouter ({item.link}): {err}")

    msg = build_message(ranked, now, args.limit, ru_summaries=ru_summaries)

    if args.dry_run:
        print(msg)
        if errors:
            print("\nFeed warnings:")
            for err in errors:
                print(f"- {err}")
    else:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.")
            return 2
        send_telegram(msg, token, chat_id)
        print(f"Sent {min(len(ranked), args.limit)} items to Telegram.")
        if errors:
            print("Feed warnings:")
            for err in errors:
                print(f"- {err}")

    # update seen links (cap growth) only for real runs
    if not args.dry_run:
        new_seen = list(seen_links.union({item.link for item in ranked[: args.limit]}))
        state["seen_links"] = new_seen[-5000:]
        # keep cache bounded by recency of activity
        if summary_cache:
            recent_links = list({item.link for item in ranked[:500]}.union(state["seen_links"][-1000:]))
            state["summary_cache"] = {k: v for k, v in summary_cache.items() if k in recent_links}
        save_state(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
