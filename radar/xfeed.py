"""판매처 X(트위터) 계정에서 응모 기간 읽기 — FxTwitter 공개 API 사용 (로그인·계정 불필요).

점프업이엔티처럼 응모 기간을 사이트가 아닌 X에만 올리는 곳을 위해 쓴다.
트윗을 아티스트 이름 + 행사 날짜로 공지와 짝지어 응모 기간·당첨 발표를 채운다.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime

import requests

from .common import ROOT
from .ocr import SINGLE, _mk, normalize

API = "https://api.fxtwitter.com/2/profile/{handle}/statuses"
CACHE = os.path.join(ROOT, "data", "x_cache.json")
CACHE_TTL = 50 * 60      # 50분 (매시 수집 때 한 번씩만 새로 받기)
PAGES = 3                # 한 번에 최근 약 60개 트윗

# 판매처 → X 계정
X_ACCOUNTS = {"점프업이엔티": "JumpUp_ent", "디어마이뮤즈": "dearmymuse_forU"}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fansign-radar/1.0; personal use)"}


def fetch_tweets(handle: str) -> list[dict]:
    try:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(handle)
    if hit and time.time() - hit.get("_t", 0) < CACHE_TTL:
        return hit["tweets"]
    tweets, cursor = [], None
    for _ in range(PAGES):
        r = requests.get(API.format(handle=handle), params={"cursor": cursor} if cursor else None,
                         headers=HEADERS, timeout=20)
        if r.status_code != 200:
            break
        d = r.json()
        for t in d.get("results") or []:
            tweets.append({"id": t.get("id"), "url": t.get("url"), "text": t.get("text") or "",
                           "ts": t.get("created_timestamp")})
        cursor = (d.get("cursor") or {}).get("bottom")
        if not cursor:
            break
        time.sleep(1.0)
    if tweets:
        cache[handle] = {"_t": time.time(), "tweets": tweets}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    elif hit:
        return hit["tweets"]   # 실패하면 지난 결과라도
    return tweets


def _first_dt(seg: str, end=False):
    for m in SINGLE.finditer(seg):
        dt = _mk(m.groups(), end=end)
        if dt:
            return dt
    return None


def parse_tweet(text: str) -> dict:
    """'이벤트 일시 : 11/29 (일) 13:00', '응모 기간 : ~11/26 (목) 23:59', '판매 기간 : 11/20 ~ 11/22' 등."""
    t = normalize(text)
    out: dict = {}
    m = re.search(r"(?:이벤트|사인회|행사)\s*(?:일시|일정)\s*[:：]?|진행\s*(?:일시|일정)?\s*[:：]", t)
    if m:
        dt = _first_dt(t[m.end(): m.end() + 40])
        if dt:
            out["event_date"] = dt.date()
    m = re.search(r"(?:응모|판매|구매)\s*(?:기간|일정)\s*[:：]?|응모\s*[:：]", t)
    if m:
        seg = t[m.end(): m.end() + 90].split("\n")[0]
        if re.match(r"\s*~", seg):                      # '~11/26 23:59' → 마감만
            out["apply_end"] = _first_dt(seg, end=True)
        else:
            parts = re.split(r"~|-(?=\s*\d)", seg, maxsplit=1)
            if len(parts) == 2:
                out["apply_start"] = _first_dt(parts[0])
                out["apply_end"] = _first_dt(parts[1], end=True)
            else:
                out["apply_end"] = _first_dt(seg, end=True)
    m = re.search(r"당첨\s*자?\s*발표\s*[:：]?", t)
    if m:
        w = _first_dt(t[m.end(): m.end() + 40])
        if w:
            out["winner"] = w
    return {k: v for k, v in out.items() if v}


def _key(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def _names(artist: str) -> list[str]:
    """'idntt (아이덴티티)' → ['idntt', '아이덴티티']"""
    m = re.match(r"^(.+?)\s*\(([^()]+)\)$", artist or "")
    parts = [m.group(1), m.group(2)] if m else [artist]
    return [k for k in (_key(p) for p in parts) if len(k) >= 2]


def tweet_title(text: str) -> str:
    """트윗 첫 문단(빈 줄 전까지, 최대 3줄)에서 해시태그 기호·이모지를 뺀 제목."""
    lines = []
    for ln in text.splitlines():
        if not ln.strip():
            if lines:
                break
            continue
        lines.append(ln)
        if len(lines) == 3:
            break
    line = " ".join(lines).replace("#", " ").replace("_", " ")
    line = re.sub(r"[^\w\s\[\]()<>&:'’‘.,/!+\-]", " ", line)
    return " ".join(line.split())


def apply_x(events: list[dict]) -> tuple[int, list[tuple[str, dict]]]:
    """X 계정이 있는 판매처 공지에 트윗의 응모 기간을 채운다.

    돌려주는 값: (채운 공지 수, 사이트 목록엔 없고 X에만 있는 팬싸 공지 [(판매처, 원자료)])
    """
    filled, extra = 0, []
    for store, handle in X_ACCOUNTS.items():
        try:
            tweets = fetch_tweets(handle)
        except Exception:  # noqa: BLE001
            continue
        parsed = [(t, parse_tweet(t["text"])) for t in tweets]
        parsed = [(t, p) for t, p in parsed if p.get("apply_end")]
        store_evs = [e for e in events if e["store"] == store]
        # 사이트 공지와 짝이 안 되는 팬싸 트윗 → 따로 공지로 (같은 행사 알림 트윗은 하나만)
        seen_keys = set()
        for t, p in parsed:
            if not p.get("event_date"):
                continue
            title = tweet_title(t["text"])
            tk = _key(t["text"])
            matched = any(p["event_date"] == e.get("event_date") and any(n in tk for n in _names(e.get("artist", "")))
                          for e in store_evs)
            k = (_key(re.sub(r"D-?DAY|마감|응모", "", title)), p["event_date"])
            if matched or k in seen_keys:
                continue
            seen_keys.add(k)
            extra.append((store, {"title": title, "url": t["url"], "row": t["text"], "event_date": p["event_date"],
                                  "start": p.get("apply_start"), "end": p["apply_end"], "winner": p.get("winner"),
                                  "no_detail": True, "x_text": t["text"]}))
        evs = [e for e in store_evs if not e.get("apply_end")]
        for ev in evs:
            names = _names(ev.get("artist", "")) + _names(ev.get("artist_raw", ""))
            evd = ev.get("event_date") if isinstance(ev.get("event_date"), date) else None
            best = None
            for t, p in parsed:          # 트윗은 최신순
                tk = _key(t["text"])
                if not any(n in tk for n in names):
                    continue
                if evd and p.get("event_date") and p["event_date"] != evd:
                    continue
                best = (t, p)
                break
            if not best:
                continue
            t, p = best
            ev["apply_end"] = p["apply_end"]
            ev["apply_start"] = p.get("apply_start")
            if p.get("winner") and not ev.get("winner"):
                ev["winner"] = p["winner"]
            if not evd and p.get("event_date"):
                ev["event_date"] = p["event_date"]
            ev["schedule_src"] = "x"
            ev["schedule_url"] = t["url"]
            filled += 1
    return filled, extra
