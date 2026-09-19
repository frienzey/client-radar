"""Monitoring engine: fetch keyword mentions from Reddit + Hacker News,
score buying intent (LLM when the buyer configured a key, heuristic fallback),
and generate outreach drafts for high-intent mentions.

Usage:
    python monitor.py --once        # single poll cycle over all keywords
"""
import argparse
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from db import get_db, get_setting, utcnow

UA = {"User-Agent": "ClientRadar/1.0 (buying-intent monitor; contact: support)"}
PULLPUSH_SUB = "https://api.pullpush.io/reddit/search/submission/"
REDDIT_RSS = "https://www.reddit.com/search.rss"
HN_SEARCH = "https://hn.algolia.com/api/v1/search"


def make_client():
    """httpx client with explicit, validated proxy config.

    (This sandbox's NO_PROXY value breaks httpx's env parsing, so we bypass
    trust_env and pass only a validated proxy URL. On normal hosts the proxy
    env vars parse fine and this behaves identically.)

    EXTRA_CA_BUNDLE: optional path to an extra CA PEM (e.g. a TLS-intercepting
    egress proxy's CA in locked-down sandboxes). Documented in README.
    """
    import ssl

    proxy = None
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(k, "").strip()
        if v and "://" in v:
            try:
                httpx.URL(v)  # validate parseable
                proxy = v
                break
            except Exception:
                continue
    ctx = ssl.create_default_context()
    extra_ca = os.environ.get("EXTRA_CA_BUNDLE", "").strip() or os.environ.get(
        "SSL_CERT_FILE", "").strip()
    if extra_ca and os.path.exists(extra_ca):
        ctx.load_verify_locations(cafile=extra_ca)
    return httpx.Client(trust_env=False, proxy=proxy, verify=ctx,
                        timeout=30, headers=UA)

# --- heuristic fallback scoring -------------------------------------------
# (text, weight)
SIGNALS = [
    ("looking for", 4), ("need help", 4), ("need someone", 4), ("seeking", 3),
    ("hire", 3), ("hiring", 3), ("freelancer", 2), ("contractor", 2),
    ("recommend", 2), ("recommendation", 2), ("anyone know", 2),
    ("budget", 2), ("quote", 2), ("proposal", 2),
    ("dm me", 1), ("pm me", 1), ("asap", 1), ("urgent", 1),
    ("how much", 1), ("pay", 1),
]
# Negative signals: suppliers advertising themselves, not buyers.
ANTI_SIGNALS = [
    ("for hire", 6), ("selling", 2), ("my portfolio", 1),
]


def heuristic_score(text: str):
    t = text.lower()
    hits = [s for s, _ in SIGNALS if s in t]
    anti = [s for s, _ in ANTI_SIGNALS if s in t]
    score = sum(w for s, w in SIGNALS if s in t) - sum(w for s, w in ANTI_SIGNALS if s in t)
    score = max(0, min(10, score))
    rationale = (
        "Heuristic fallback (no LLM key configured). Matched intent signals: "
        + (", ".join(f'"{h}"' for h in hits) if hits else "none")
        + ("; discounted: " + ", ".join(f'"{a}"' for a in anti) if anti else "")
    )
    return score, rationale


# --- LLM scoring ------------------------------------------------------------
SCORE_SYSTEM = (
    "You score buying intent in forum posts for freelancers and agencies hunting "
    "for clients. Reply with JSON only: {\"score\": <0-10 integer>, "
    "\"rationale\": \"<one sentence>\"}. 10 = explicitly looking to hire/pay "
    "someone right now. 5 = open problem a freelancer could solve, no explicit "
    "hiring ask. 0 = no commercial intent."
)

DRAFT_SYSTEM = (
    "Write a short, genuine forum reply (under 80 words) from a freelancer "
    "offering help, referencing the post specifically. Warm, human, no hype, "
    "no links, no emojis. Plain text only."
)


def llm_chat(endpoint: str, api_key: str, model: str, system: str, user: str,
             timeout: int = 30) -> str:
    url = endpoint.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
    }
    with make_client() as client:
        r = client.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def score_mention(title: str, body: str):
    """Return (score:int, rationale:str, scored_by:str). Never raises."""
    text = f"{title}\n{body}".strip()[:3000]
    endpoint = get_setting("llm_endpoint", "").strip()
    api_key = get_setting("llm_api_key", "").strip()
    model = get_setting("llm_model", "gpt-4o-mini").strip() or "gpt-4o-mini"
    if endpoint and api_key:
        try:
            raw = llm_chat(endpoint, api_key, model, SCORE_SYSTEM, text)
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            score = max(0, min(10, int(data.get("score", 0))))
            rationale = str(data.get("rationale", ""))[:280]
            return score, rationale, f"llm:{model}"
        except Exception as e:  # fall through to heuristic, never break the cycle
            print(f"[score] LLM failed ({e}); using heuristic fallback")
    score, rationale = heuristic_score(text)
    return score, rationale, "heuristic"


def make_draft(title: str, body: str, keyword: str):
    """Return outreach draft string (may be empty). Never raises."""
    text = f"{title}\n{body}".strip()[:1500]
    endpoint = get_setting("llm_endpoint", "").strip()
    api_key = get_setting("llm_api_key", "").strip()
    model = get_setting("llm_model", "gpt-4o-mini").strip() or "gpt-4o-mini"
    if endpoint and api_key:
        try:
            return llm_chat(
                endpoint, api_key, model, DRAFT_SYSTEM,
                f"Keyword: {keyword}\nPost:\n{text}",
            ).strip()
        except Exception as e:
            print(f"[draft] LLM failed ({e}); using template fallback")
    return (
        f"Saw your post about {keyword} — this is exactly what I help clients "
        f"with. Happy to share how I'd approach it, no pitch. "
        f"Want me to send over a quick outline?"
    )


# --- sources -----------------------------------------------------------------
# Reddit anonymous search.json is widely 403-blocked, so we use two no-auth
# fallbacks: PullPush's submission search API (JSON) first, Reddit's own
# search.rss (Atom) second. Both are public, no credentials.


def fetch_reddit_pullpush(keyword: str, limit: int = 25):
    params = {
        "q": keyword, "sort": "desc", "size": limit,
        "fields": "title,selftext,permalink,author,created_utc,id",
    }
    url = PULLPUSH_SUB + "?" + urllib.parse.urlencode(params)
    with make_client() as client:
        r = client.get(url)
    r.raise_for_status()
    payload = r.json()
    if "data" not in payload:
        raise RuntimeError(f"pullpush error: {str(payload)[:120]}")
    out = []
    for d in payload["data"]:
        out.append({
            "source": "reddit",
            "source_id": "pp_" + str(d.get("id", "")),
            "title": d.get("title", "") or "",
            "body": (d.get("selftext", "") or "")[:2000],
            "url": "https://www.reddit.com" + (d.get("permalink", "") or ""),
            "author": d.get("author", "") or "",
            "created_utc": int(d.get("created_utc", 0) or 0),
        })
    return out


def _atom_text(el):
    return (el.text or "").strip() if el is not None else ""


def fetch_reddit_rss(keyword: str, limit: int = 25):
    params = {"q": keyword, "sort": "new", "limit": limit, "t": "week"}
    url = REDDIT_RSS + "?" + urllib.parse.urlencode(params)
    with make_client() as client:
        r = client.get(url)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall("a:entry", ns):
        link = e.find("a:link", ns)
        href = link.get("href", "") if link is not None else ""
        author = _atom_text(e.find("a:author/a:name", ns))
        updated = _atom_text(e.find("a:updated", ns))
        try:
            ts = int(datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp())
        except Exception:
            ts = 0
        content = _atom_text(e.find("a:content", ns))
        body = html.unescape(re.sub(r"<[^>]+>", " ", content)).strip()[:2000]
        out.append({
            "source": "reddit",
            "source_id": "rss_" + re.sub(r"\W+", "", href)[-24:],
            "title": _atom_text(e.find("a:title", ns)),
            "body": body,
            "url": href,
            "author": author,
            "created_utc": ts,
        })
    return out


def fetch_reddit(keyword: str, limit: int = 25):
    """Reddit mentions via PullPush, falling back to Reddit RSS."""
    try:
        return fetch_reddit_pullpush(keyword, limit)
    except Exception as e:
        print(f"[reddit] pullpush failed ({e}); trying RSS")
        return fetch_reddit_rss(keyword, limit)


def fetch_hn(keyword: str, limit: int = 25):
    params = {"query": keyword, "tags": "comment", "hitsPerPage": limit}
    url = HN_SEARCH + "?" + urllib.parse.urlencode(params)
    with make_client() as client:
        r = client.get(url)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        body = html.unescape(re.sub(r"<[^>]+>", " ", h.get("comment_text") or ""))
        ts = int(h.get("created_at_i", 0) or 0)
        if ts and ts < time.time() - 90 * 86400:
            continue  # skip stale HN threads
        out.append({
            "source": "hackernews",
            "source_id": "hn_" + str(h.get("objectID", "")),
            "title": (h.get("story_title") or "")[:160],
            "body": body.strip()[:2000],
            "url": f"https://news.ycombinator.com/item?id={h.get('story_id') or h.get('objectID')}",
            "author": h.get("author", ""),
            "created_utc": ts,
        })
    return out


# --- poll cycle ---------------------------------------------------------------
DRAFT_THRESHOLD = 7


def poll_once():
    conn = get_db()
    kws = conn.execute("SELECT id, text FROM keywords ORDER BY id").fetchall()
    conn.close()
    total_new, total_scored = 0, 0
    for kw in kws:
        items = []
        for fetcher in (fetch_reddit, fetch_hn):
            try:
                items.extend(fetcher(kw["text"]))
                time.sleep(1)  # be polite to public endpoints
            except Exception as e:
                print(f"[poll] {fetcher.__name__} failed for '{kw['text']}': {e}")
        for it in items:
            conn = get_db()
            exists = conn.execute(
                "SELECT 1 FROM mentions WHERE source_id=? OR (url <> '' AND url=?)",
                (it["source_id"], it["url"]),
            ).fetchone()
            if exists:
                conn.close()
                continue
            score, rationale, scored_by = score_mention(it["title"], it["body"])
            draft = make_draft(it["title"], it["body"], kw["text"]) if score >= DRAFT_THRESHOLD else ""
            conn.execute(
                """INSERT INTO mentions(source, source_id, keyword_id, title, body, url,
                                        author, created_utc, score, rationale, draft,
                                        scored_by, fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it["source"], it["source_id"], kw["id"], it["title"], it["body"],
                 it["url"], it["author"], it["created_utc"], score, rationale,
                 draft, scored_by, utcnow()),
            )
            conn.commit()
            conn.close()
            total_new += 1
            total_scored += 1
    print(f"[poll] done: {total_new} new mentions stored, {total_scored} scored")
    return total_new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one poll cycle")
    args = ap.parse_args()
    if args.once:
        poll_once()
    else:
        ap.print_help()
