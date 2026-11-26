"""Gather news mentions from Google News, Hacker News, and NVIDIA blogs.

New items are merged into site/data/news.json (deduped by URL). Classification
happens separately in classify_news.py so a missing API key never blocks this.
"""

import hashlib
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

from common import DATA, http, load_config, log, read_json, write_json

NOW = datetime.now(timezone.utc)


def item(title, url, source, published, origin, snippet=""):
    title = unescape(re.sub(r"<[^>]+>", "", title or "")).strip()
    snippet = unescape(re.sub(r"<[^>]+>", " ", snippet or ""))
    snippet = re.sub(r"\s+", " ", snippet).strip()[:400]
    return {
        "id": hashlib.sha1(url.encode()).hexdigest()[:16],
        "title": title,
        "url": url,
        "source": source,
        "published": published.astimezone(timezone.utc).isoformat() if published else None,
        "origin": origin,
        "snippet": snippet,
        "first_seen": NOW.isoformat(),
    }


def parse_date(text):
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None


def rss_items(raw):
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        yield {
            "title": it.findtext("title") or "",
            "link": (it.findtext("link") or "").strip(),
            "date": parse_date(it.findtext("pubDate")),
            "desc": it.findtext("description") or "",
            "source": it.findtext("source"),
            "cats": [c.text or "" for c in it.findall("category")],
        }


def google_news(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{query} when:30d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )
    raw, _ = http(url)
    out = []
    for it in rss_items(raw):
        title = it["title"]
        source = it["source"] or "Google News"
        # Google appends " - Publisher" to titles.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append(item(title, it["link"], source, it["date"], f"google:{query}"))
    return out


def hacker_news(query, since):
    url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(
        {"query": query, "tags": "story", "advancedSyntax": "true",
         "restrictSearchableAttributes": "title,url",
         "numericFilters": f"created_at_i>{int(since.timestamp())}"}
    )
    raw, _ = http(url)
    out = []
    for h in json.loads(raw)["hits"]:
        discussion = f"https://news.ycombinator.com/item?id={h['objectID']}"
        it = item(h.get("title"), h.get("url") or discussion, "Hacker News",
                  parse_date(h.get("created_at")), f"hn:{query}")
        it["discussion_url"] = discussion
        it["points"] = h.get("points")
        out.append(it)
    return out


def blog_feed(url, keywords):
    raw, _ = http(url)
    host = urllib.parse.urlparse(url).netloc
    pattern = re.compile("|".join(re.escape(k) for k in keywords))
    out = []
    for it in rss_items(raw):
        text = " ".join([it["title"], it["desc"], *it["cats"]])
        if pattern.search(text):
            out.append(item(it["title"], it["link"], host, it["date"], f"blog:{host}", it["desc"]))
    return out


def main():
    cfg = load_config()["news"]
    since = NOW - timedelta(days=cfg["lookback_days"])
    jobs = (
        [(google_news, q) for q in cfg["google_news_queries"]]
        + [(lambda q: hacker_news(q, NOW - timedelta(days=30)), q) for q in cfg["hn_queries"]]
        + [(lambda u: blog_feed(u, cfg["blog_keywords"]), u) for u in cfg["blog_feeds"]]
    )

    found = []
    for fn, arg in jobs:
        try:
            got = fn(arg)
            log(f"  {len(got):3d}  {arg}")
            found += got
        except Exception as e:  # sources are best-effort; one failing feed is fine
            log(f"  ERR  {arg}: {e}")

    existing = {n["id"]: n for n in read_json(DATA / "news.json", {"items": []})["items"]}
    added = 0
    for n in found:
        if n["id"] in existing:
            prev = existing[n["id"]]
            if n["origin"] not in prev.get("origins", [prev["origin"]]):
                prev.setdefault("origins", [prev["origin"]]).append(n["origin"])
            continue
        n["origins"] = [n["origin"]]
        existing[n["id"]] = n
        added += 1

    def when(n):
        return n["published"] or n["first_seen"]

    # Comma-separated, kept in a secret so the terms stay out of the public repo.
    terms = [t.strip() for t in os.environ.get("NEWS_EXCLUDE_TERMS", "").split(",") if t.strip()]
    excluded = re.compile("|".join(re.escape(t) for t in terms) or "$^", re.I)

    def keep(n):
        text = " ".join([n["title"], n["snippet"], n["source"], n["url"]])
        return when(n) >= since.isoformat() and not excluded.search(text)

    items = [n for n in existing.values() if keep(n)]
    items.sort(key=when, reverse=True)
    write_json(DATA / "news.json", {"generated_at": NOW.isoformat(), "items": items})
    log(f"News: {added} new, {len(items)} kept")


if __name__ == "__main__":
    main()
