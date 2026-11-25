"""텔레그램 알림. 봇 토큰과 채팅 ID는 config.json에만 저장한다 (외부로 보내는 곳은 api.telegram.org뿐)."""
from __future__ import annotations

import html
import time
from datetime import date, datetime

import requests

API = "https://api.telegram.org/bot{token}/{method}"
WEEK = "월화수목금토일"


def call(token: str, method: str, **params):
    r = requests.post(API.format(token=token, method=method), json=params, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"HTTP {r.status_code}")
    return data["result"]


def get_me(token: str) -> dict:
    return call(token, "getMe")


def find_chat(token: str) -> dict | None:
    """봇에게 최근 말을 건 채팅을 찾는다 (사용자가 봇에게 /start를 보낸 뒤)."""
    updates = call(token, "getUpdates", limit=50, timeout=0)
    for u in reversed(updates):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
            return {"id": chat["id"], "name": name or chat.get("username") or str(chat["id"])}
    return None


def send(token: str, chat_id, text: str):
    return call(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                disable_web_page_preview=True)


def _d(v) -> str:
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v) if "T" in v else date.fromisoformat(v)
        except ValueError:
            return v
    if isinstance(v, datetime):
        t = f"{v.month}/{v.day}({WEEK[v.weekday()]})"
        return t + (f" {v:%H:%M}" if (v.hour, v.minute) not in ((0, 0), (23, 59)) else "")
    if isinstance(v, date):
        return f"{v.month}/{v.day}({WEEK[v.weekday()]})"
    return ""


def format_event(ev: dict) -> str:
    e = html.escape
    tags = " · ".join(ev.get("formats") or [])
    if ev.get("public"):
        tags += " · 공개" + (f"({ev['venue']})" if ev.get("venue") else "")
    if ev.get("overseas"):
        tags += " · 해외"
    if ev.get("last_fansign"):
        tags += " · 마지막 팬싸"
    lines = [f"🆕 <b>{e(ev['artist'])}</b>  <i>{e(ev['store'])}</i>", e(ev["title"])]
    if ev.get("event_date"):
        lines.append(f"📅 행사 {_d(ev['event_date'])}  {e(tags)}")
    elif tags:
        lines.append(f"🏷 {e(tags)}")
    if ev.get("apply_end"):
        lines.append(f"📝 응모 ~{_d(ev['apply_end'])}")
    if ev.get("winner"):
        lines.append(f"🎉 발표 {_d(ev['winner'])}")
    lines.append(f'<a href="{e(ev["url"])}">원문 보기</a>')
    return "\n".join(lines)


def pick(events: list[dict], cfg: dict) -> list[dict]:
    from .collect import matches_favorite
    out = []
    for ev in events:
        if ev.get("overseas") and not cfg.get("include_overseas_alert"):
            continue
        if cfg.get("notify") == "all" or matches_favorite(ev, cfg.get("favorites", [])):
            out.append(ev)
    return out


def notify_new(events: list[dict], cfg: dict) -> int:
    tg = cfg.get("telegram") or {}
    if not (tg.get("enabled") and tg.get("token") and tg.get("chat_id")):
        return 0
    chosen = pick(events, cfg)
    if not chosen:
        return 0
    from .collect import iso
    chosen = [{k: iso(v) for k, v in ev.items()} for ev in chosen]
    sent = 0
    if len(chosen) > 12:
        # 너무 많으면 묶어서 한 통
        head = f"🆕 새 팬싸 공지 {len(chosen)}건\n\n"
        body = "\n".join(f"• <b>{html.escape(ev['artist'])}</b> ({html.escape(ev['store'])}) "
                         f"{_d(ev.get('event_date')) if ev.get('event_date') else ''}" for ev in chosen[:60])
        send(tg["token"], tg["chat_id"], head + body)
        return len(chosen)
    for ev in chosen:
        send(tg["token"], tg["chat_id"], format_event(ev))
        sent += 1
        time.sleep(0.6)
    return sent
