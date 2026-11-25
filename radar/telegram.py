"""텔레그램 알림. 봇 토큰과 채팅 ID는 config.json에만 저장한다 (외부로 보내는 곳은 api.telegram.org뿐)."""
from __future__ import annotations

import html
import re
import time
from datetime import date, datetime, timedelta

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


def notify_subscribers(events: list[dict], token: str, subs_url: str, subs_key: str) -> tuple[int, int]:
    """봇 구독자별 알림 (서버 수집용). 구독 목록은 Cloudflare 봇(/subscribers)에서 받아 온다.

    돌려주는 값: (알림 받은 사람 수, 보낸 메시지 수)
    """
    r = requests.get(subs_url, headers={"Authorization": f"Bearer {subs_key}"}, timeout=20)
    r.raise_for_status()
    subs = r.json().get("subscribers", [])
    from .collect import iso
    people = msgs = 0
    gone = []   # 봇을 차단했거나 탈퇴한 사람
    for s in subs:
        cfg = {"favorites": s.get("artists", []), "notify": "all" if s.get("all") else "favorites",
               "include_overseas_alert": s.get("overseas", False)}
        chosen = [{k: iso(v) for k, v in ev.items()} for ev in pick(events, cfg)]
        if not chosen:
            continue
        try:
            if len(chosen) > 8:
                head = f"🆕 새 팬싸 공지 {len(chosen)}건\n\n"
                body = "\n".join(f"• <b>{html.escape(ev['artist'])}</b> ({html.escape(ev['store'])}) "
                                 f"{_d(ev.get('event_date')) if ev.get('event_date') else ''}" for ev in chosen[:60])
                send(token, s["chat_id"], head + body)
                msgs += 1
            else:
                for ev in chosen:
                    send(token, s["chat_id"], format_event(ev))
                    msgs += 1
                    time.sleep(0.05)
            people += 1
        except Exception as e:  # noqa: BLE001
            if re.search(r"blocked by the user|user is deactivated|chat not found", str(e), re.I):
                gone.append(s["chat_id"])
            continue
    if gone:
        try:
            requests.post(subs_url.rsplit("/", 1)[0] + "/unsubscribe", json={"chat_ids": gone},
                          headers={"Authorization": f"Bearer {subs_key}"}, timeout=20)
            from .collect import log
            log(f"  봇을 차단한 구독자 {len(gone)}명 정리")
        except Exception:  # noqa: BLE001
            pass
    return people, msgs


def send_admin(text: str) -> bool:
    """관리자(봇 ADMIN_CHAT_ID)에게 한 통 — 테스트 알림·수집 이상 알림용. 서버 환경 변수로 동작."""
    import os
    token, url, key = (os.environ.get(k, "") for k in ("TELEGRAM_TOKEN", "SUBS_URL", "SUBS_KEY"))
    if not (token and url and key):
        return False
    admin = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20).json().get("admin")
    if not admin:
        return False
    send(token, admin, text)
    return True


# ── 마감·발표 알림 (서버 수집이 매시 확인, 사람마다 한 통) ──
def _first_seen(ev: dict):
    v = str(ev.get("first_seen") or "").split("@")[-1]
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def due_reminders(events: list[dict], now: datetime, sent: dict) -> list[tuple[str, dict, str]]:
    """지금 보낼 차례인 (종류, 공지, 기록 키). 종류: end(응모 마감) / win(당첨자 발표).
    마감: 낮 12시 이후 마감이면 당일 오전 9시, 오전 마감이면 전날 저녁 8시. 발표: 당일 오전 9시(그보다 이른 발표면 1시간 전)."""
    out = []
    for ev in events:
        end = ev.get("apply_end")
        if isinstance(end, datetime) and ev.get("status") == "open" and end > now:
            key = f"end:{ev['id']}:{end.date()}"
            target = end.replace(hour=9, minute=0) if end.hour >= 12 else (end - timedelta(days=1)).replace(hour=20, minute=0)
            if now >= target and key not in sent:
                fs = _first_seen(ev)
                if fs and now - fs < timedelta(hours=3):
                    sent[key] = now.isoformat(timespec="minutes")   # 방금 새 공지 알림을 받음 → 마감 알림은 건너뜀
                else:
                    out.append(("end", ev, key))
        w = ev.get("winner")
        if isinstance(w, datetime):
            dateonly = (w.hour, w.minute) == (0, 0)
            target = w.replace(hour=9, minute=0) if dateonly else min(w.replace(hour=9, minute=0), w - timedelta(hours=1))
            until = w + timedelta(days=1) if dateonly else w
            key = f"win:{ev['id']}:{w.date()}"
            if target <= now < until and key not in sent:
                out.append(("win", ev, key))
    return out


def _when(v: datetime, now: datetime) -> str:
    day = "오늘" if v.date() == now.date() else "내일" if v.date() == (now + timedelta(days=1)).date() else f"{v.month}/{v.day}({WEEK[v.weekday()]})"
    return day + ("" if (v.hour, v.minute) == (0, 0) else f" {v:%H:%M}")


def reminder_message(items: list[tuple[str, dict, str]], now: datetime) -> str:
    e = html.escape
    parts = []
    for kind, head in (("end", "⏰ <b>응모 마감 알림</b>"), ("win", "🎉 <b>당첨자 발표 알림</b>")):
        rows = [ev for k, ev, _ in items if k == kind]
        if not rows:
            continue
        lines, seen = [head], set()
        for ev in rows[:40]:
            when = _when(ev["apply_end"] if kind == "end" else ev["winner"], now)
            if (ev["artist"], ev["store"], when) in seen:   # 같은 팬싸의 공지 여러 개(1:1 / 릴레이 등)는 한 줄
                continue
            seen.add((ev["artist"], ev["store"], when))
            extra = f" · 행사 {_d(ev['event_date'])}" if ev.get("event_date") else ""
            verb = "마감" if kind == "end" else "발표"
            lines.append(f'• <b>{e(ev["artist"])}</b> ({e(ev["store"])}) — <b>{when} {verb}</b>{extra} '
                         f'<a href="{e(ev["url"])}">원문</a>')
        parts.append(chr(10).join(lines))
    parts.append("<i>마감·발표 알림 끄기: /remind</i>")
    return (chr(10) * 2).join(parts)


def send_reminders(events: list[dict], now: datetime, sent: dict, token: str, subs_url: str, subs_key: str) -> tuple[int, int]:
    """돌려주는 값: (알림 받은 사람 수, 이번에 알린 공지 수). sent는 보낸 기록(공지 단위)으로 갱신된다."""
    due = due_reminders(events, now, sent)
    if not due:
        return 0, 0
    r = requests.get(subs_url, headers={"Authorization": f"Bearer {subs_key}"}, timeout=20)
    r.raise_for_status()
    people = 0
    for s in r.json().get("subscribers", []):
        if s.get("remind") is False:
            continue
        cfg = {"favorites": s.get("artists", []), "notify": "all" if s.get("all") else "favorites",
               "include_overseas_alert": s.get("overseas", False)}
        mine = pick([ev for _k, ev, _key in due], cfg)
        ids = {id(ev) for ev in mine}
        items = [d for d in due if id(d[1]) in ids]
        if not items:
            continue
        try:
            send(token, s["chat_id"], reminder_message(items, now))
            people += 1
            time.sleep(0.05)
        except Exception:  # noqa: BLE001
            continue
    stamp = now.isoformat(timespec="minutes")
    for _k, _ev, key in due:
        sent[key] = stamp
    return people, len(due)

