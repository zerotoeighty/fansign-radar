"""수집 본체: 18곳을 돌고 → 팬싸만 골라 → 지난 공지 빼고 → data/events.json 저장 → 새 공지 텔레그램 알림."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import telegram
from .common import (KST, PUBLIC, ROOT, Blocked, artist_from, classify, fetch, find_venue, labelled_datetime,
                     labelled_period, now_kst, page_text, render, title_event_date, today_kst)
from .sources import SOURCES

DATA = os.path.join(ROOT, "data")
EVENTS = os.path.join(DATA, "events.json")
SEEN = os.path.join(DATA, "seen.json")
DETAIL_CACHE = os.path.join(DATA, "detail_cache.json")
CONFIG = os.path.join(ROOT, "config.json")
LOCK = os.path.join(DATA, "collect.lock")
LOG = os.path.join(DATA, "collect.log")
PC_EVERLINE = os.path.join(DATA, "pc_everline.json")   # PC가 수집한 에버라인 공지 (서버에서 막히는 판매처)

# 서버(GitHub Actions)에서 돌 때: 에버라인은 해외·클라우드 IP를 막으므로 PC가 올린 결과를 쓴다
CLOUD = os.environ.get("FANSIGN_CLOUD") == "1"
PC_ONLY_STORES = {"에버라인"}

POSTED_KEEP_DAYS = 21      # 날짜가 제목에 없는 공지는 올라온 지 이 기간까지만 보여 준다
DETAIL_TTL_HOURS = 12


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def log(msg: str):
    line = f"[{now_kst():%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        pass
    os.makedirs(DATA, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    # 로그는 최근 2000줄만
    try:
        with open(LOG, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 2500:
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-2000:])
    except OSError:
        pass


def iso(v):
    if isinstance(v, datetime):
        return v.astimezone(KST).isoformat(timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    return v


# ───────────────────────── 1. 판매처 돌기 ─────────────────────────

def run_source(entry):
    name, page, fn = entry
    t0 = time.time()
    try:
        res = fn()
        return name, page, res, None, time.time() - t0
    except Blocked as e:
        return name, page, None, ("blocked", str(e)), time.time() - t0
    except Exception as e:  # noqa: BLE001
        return name, page, None, ("error", f"{type(e).__name__}: {e}"[:200]), time.time() - t0


# ───────────────────────── 2. 공지 → 팬싸 카드 ─────────────────────────

def build_event(store: str, raw: dict) -> dict | None:
    title = raw["title"]
    winner_posted = bool(raw.get("winner_posted"))
    judge = re.sub(r"\s*(?:이벤트\s*)?당첨\s*자?\s*발표", "", title) if winner_posted else title
    kind = classify(judge, raw.get("hint", ""))
    if not kind:
        return None
    ev_date = raw.get("event_date") or title_event_date(title)
    url = raw["url"]
    fmt = []
    if kind["meet"]:
        fmt.append("대면")
    if kind["call"]:
        fmt.append("영상통화")
    if raw.get("formats"):       # 판매처 규칙으로 정한 형식(프롬스토어 MEET & CALL 등)이 있으면 그걸로
        fmt = list(raw["formats"])
    return {
        "id": hashlib.sha1(f"{store}|{url}|{title}".encode()).hexdigest()[:12],
        "store": store,
        "artist": raw.get("artist") or artist_from(title),
        "title": title,
        "url": url,
        "formats": fmt,
        "overseas": kind["overseas"],
        "public": kind["public"],
        "last_fansign": bool(LAST_FANSIGN.search(title)),
        "venue": None,
        "event_date": ev_date,
        "apply_start": raw.get("start"),
        "apply_end": raw.get("end"),
        "winner": raw.get("winner"),
        "posted": raw.get("posted"),
        "_current_list": raw.get("current_list", False),
        "_no_detail": raw.get("no_detail", False),
        "_render_detail": raw.get("render_detail", False),
        "_explicit": kind["explicit"],
        "winner_posted": winner_posted,
    }


CLOSED_KEEP_DAYS = 3   # 행사일을 모르는 마감 공지는 마감 뒤 며칠만 보여 준다


def is_live(ev: dict, now: datetime) -> tuple[bool, str]:
    """화면에 남길지와 상태. 상태: open(응모 중) / soon(오픈 예정) / closed(응모 마감, 행사 전) / unknown(응모 기간 모름)."""
    today = now.date()
    d = ev.get("event_date")
    if isinstance(d, date):
        if d < today:
            return False, "행사 지남"
        if (d - today).days > 200:
            return False, "날짜 이상"
    if ev.get("winner_posted"):          # 당첨자 발표가 올라왔으면 응모는 끝난 것
        return (True, "closed") if isinstance(d, date) else (False, "마감 지남")
    end = ev.get("apply_end")
    if isinstance(end, datetime):
        if end < now:
            if isinstance(d, date) or (now - end).days < CLOSED_KEEP_DAYS:
                return True, "closed"
            return False, "마감 지남"
        start = ev.get("apply_start")
        if isinstance(start, datetime) and start > now:
            return True, "soon"
        return True, "open"
    if isinstance(d, date):
        return True, "unknown"
    posted = ev.get("posted")
    if isinstance(posted, date):
        return ((today - posted).days <= POSTED_KEEP_DAYS), "unknown"
    if ev.get("_current_list"):
        return True, "unknown"
    return False, "날짜 없음"


# ───────────────────────── 3. 상세 페이지 (글자로 된 일정·공개 여부) ─────────────────────────

GONE = re.compile(r"삭제된\s*(?:상품|게시물)|존재하지\s*않는\s*(?:상품|게시물|페이지)|페이지를\s*찾을\s*수\s*없|"
                  r"판매\s*종료된\s*상품")


EVENT_SEG = re.compile(r"(?:이벤트|행사|팬\s*사인회|사인회)\s*(?:일시|일정)")


def schedule_segment(text: str) -> dict:
    """'이벤트 일시 대면 팬사인회: 2026. 11. 28 (토) 14:00 / 1:1 비디오콜: …' 구간에서 행사일과 형식."""
    from .ocr import SINGLE, _mk, normalize
    m = EVENT_SEG.search(text)
    if not m:
        return {}
    seg = normalize(text[m.end(): m.end() + 260])
    seg = re.split(r"응모\s*기간|응모\s*방법|당첨", seg)[0]
    out = {}
    for dm in SINGLE.finditer(seg):
        dt = _mk(dm.groups())
        if dt and (dm.group(1) or dm.group(5)):
            out["event_date"] = dt.date().isoformat()
            break
    fm = []
    if re.search(r"(?<!비)대면|오프라인|offline|팬\s*사인회\s*[:：]", seg, re.I):
        fm.append("대면")
    if re.search(r"비디오\s*콜|영상\s*통화|영통|video\s*call", seg, re.I):
        fm.append("영상통화")
    if fm:
        out["seg_formats"] = fm
    return out


def detail_info(url: str, cache: dict, use_render: bool = False) -> dict:
    hit = cache.get(url)
    if hit and time.time() - hit.get("_t", 0) < DETAIL_TTL_HOURS * 3600:
        return hit
    info = {"_t": time.time()}
    try:
        html = render(url, budget_ms=20000) if use_render else fetch(url, timeout=15, retries=1)
        text = page_text(html)
        info.update(schedule_segment(text))
        info["gone"] = bool(GONE.search(text[:4000]))
        s, e = labelled_period(text, r"응모|구매\s*응모|판매|이벤트\s*(?:응모|진행)|예약\s*판매")
        info["start"], info["end"] = iso(s), iso(e)
        w = labelled_datetime(text, r"당첨\s*자?\s*(?:발표|공지|안내)")
        info["winner"] = iso(w)
        info["public"] = bool(PUBLIC.search(text))
        info["venue"] = find_venue(text)
        info["last"] = bool(LAST_FANSIGN.search(text))
        if "EVENT PRODUCTS" in text:   # 에버라인 상세: 행사 상품 이름에 대면/영상통화가 적혀 있다
            prods = text.split("EVENT PRODUCTS", 1)[1][:1500]
            k = classify(prods)
            if k:
                info["formats"] = [n for n, on in (("대면", k["meet"]), ("영상통화", k["call"])) if on]
    except Exception as ex:  # noqa: BLE001
        info["error"] = str(ex)[:120]
        info["_t"] = time.time() - (DETAIL_TTL_HOURS - 1) * 3600  # 실패는 1시간 뒤 다시
    cache[url] = info
    return info


def parse_iso(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None


def apply_detail(ev: dict, info: dict):
    if info.get("gone"):
        ev["_gone"] = True
        return
    if not ev.get("apply_end") and info.get("end"):
        e = parse_iso(info["end"])
        s = parse_iso(info.get("start"))
        # 상세에서 읽은 기간이 행사일보다 한참 뒤면 다른 글자를 잘못 읽은 것
        if e and (not isinstance(ev.get("event_date"), date) or e.date() <= ev["event_date"] + timedelta(days=1)):
            ev["apply_end"], ev["apply_start"] = e, s
    if not ev.get("winner") and info.get("winner"):
        ev["winner"] = parse_iso(info["winner"])
    if info.get("event_date") and not isinstance(ev.get("event_date"), date):
        ev["event_date"] = date.fromisoformat(info["event_date"])
    # '이벤트 일시' 구간 형식: 제목에 형식이 없거나(위드뮤 등) 제목 표기가 애매한 크롬 상세(헬로라이브 MEET&CALL)일 때만
    if info.get("seg_formats") and (not ev.get("_explicit") or ev.get("_render_detail")):
        ev["formats"] = info["seg_formats"]
        ev["_seg_formats"] = True
    elif info.get("formats"):
        ev["formats"] = info["formats"]
    if info.get("last"):
        ev["last_fansign"] = True
    if info.get("public"):
        ev["public"] = True
    if info.get("venue"):
        ev["venue"] = info["venue"]
        ev["public"] = True


# ───────────────────────── 4. 정리 ─────────────────────────

def restore_event(e: dict) -> dict:
    """events.json에 저장된 공지(글자 날짜)를 다시 계산할 수 있게 되돌린다."""
    ev = dict(e)
    if ev.get("event_date"):
        ev["event_date"] = date.fromisoformat(ev["event_date"])
    for k in ("apply_start", "apply_end", "winner"):
        ev[k] = parse_iso(ev.get(k))
    if ev.get("posted"):
        try:
            ev["posted"] = date.fromisoformat(ev["posted"])
        except ValueError:
            ev["posted"] = None
    ev["artist"] = ev.get("artist_raw") or ev.get("artist")
    ev.update(_current_list=True, _no_detail=True, _render_detail=False, _explicit=True, _seg_formats=True)
    return ev


def dedupe(events: list[dict]) -> list[dict]:
    out: dict[tuple, dict] = {}
    for ev in events:
        key = (ev["store"], ev["url"])
        if key in out:
            continue
        key2 = (ev["store"], re.sub(r"\W+", "", ev["title"].lower()))
        if key2 in out:
            continue
        out[key] = ev
        out[key2] = ev
    uniq = {id(v): v for v in out.values()}
    return list(uniq.values())


# ───────────────────────── 3-2. 이미지 OCR (응모 기간이 이미지에만 있을 때) ─────────────────────────

OCR_CACHE = os.path.join(DATA, "ocr_cache.json")
OCR_PER_RUN = 80          # 한 번 수집할 때 새로 읽을 최대 공지 수
OCR_IMAGES_PER_EVENT = 4
OCR_SKIP_STORES = {"헬로라이브", "메이크스타"}   # 목록·API에 일정이 이미 있음
RENDER_STORES = {"위드뮤", "후즈팬", "프롬스토어"}             # 상세 페이지도 크롬으로 열어야 함


def _ocr_sched_from_cache(hit: dict) -> dict:
    out = {}
    for k in ("apply_start", "apply_end", "winner"):
        if hit.get(k):
            out[k] = parse_iso(hit[k])
    if hit.get("event_date"):
        out["event_date"] = date.fromisoformat(hit["event_date"])
    if hit.get("formats"):
        out["formats"] = hit["formats"]
    out["last"] = bool(hit.get("last"))
    out["public"] = bool(hit.get("public"))
    out["venue"] = hit.get("venue")
    return out


LAST_FANSIGN = re.compile(r"마지막\s*(?:대면\s*|영상\s*통화\s*)?(?:팬\s*)?사인회|마지막\s*팬싸|last\s*fan\s*-?\s*sign", re.I)


def ocr_formats(text: str) -> list[str]:
    """공지 이미지 글자에서 형식. '비대면'은 대면이 아님. 영상통화 팬사인회의 '팬사인회'는 대면으로 치지 않는다."""
    t = re.sub(r"\s+", "", text)
    fm = []
    if re.search(r"(?<!비)대면|오프라인(?:팬)?사인|offline(?:fan)?sign|하이터치|hi-?touch", t, re.I):
        fm.append("대면")
    if re.search(r"영상통화|비디오콜|videocall|영통팬싸|meet&call", t, re.I):
        fm.append("영상통화")
    return fm


def apply_ocr(ev: dict, sched: dict):
    if sched.get("last"):
        ev["last_fansign"] = True
    if sched.get("venue"):
        ev["public"], ev["venue"] = True, sched["venue"]
    elif sched.get("public"):
        ev["public"] = True
    fm = sched.get("formats")
    if fm and not ev.get("_explicit") and not ev.get("_seg_formats"):
        # 제목에 형식이 없을 때(위드뮤 '팬사인회 이벤트 안내' 등) 이미지 내용으로 보충
        ev["formats"] = sorted(set(ev.get("formats") or []) | set(fm), key=["대면", "영상통화"].index)
    s, e = sched.get("apply_start"), sched.get("apply_end")
    evd = ev.get("event_date") if isinstance(ev.get("event_date"), date) else None
    if not evd and sched.get("event_date"):
        evd = sched["event_date"]
        ev["event_date"] = evd
        ev["event_date_src"] = "image"
    if e and not ev.get("apply_end") and (not s or s <= e) and (not evd or e.date() <= evd + timedelta(days=1)):
        ev["apply_start"], ev["apply_end"] = s, e
        ev["schedule_src"] = "image"
        w = sched.get("winner")
        if w and w > e and (not evd or w.date() <= evd) and not ev.get("winner"):
            ev["winner"] = w


def _ocr_mismatch(ev: dict, hit: dict) -> bool:
    """X·목록에서 얻은 응모 마감과 이미지 OCR 마감이 하루 넘게 다르면 True (예: 행사일을 응모 시작으로 읽음)."""
    a = ev.get("apply_end")
    if not isinstance(a, datetime) or not hit.get("apply_end") or ev.get("schedule_src") == "image":
        return False
    b = parse_iso(hit["apply_end"])
    return bool(b) and abs((a - b).total_seconds()) > 86400


def ocr_pass(events: list[dict], now: datetime, allow_new: bool = True) -> int:
    """이미지 일정 채우기. allow_new=False면 저장된 결과만 쓰고, 새로 읽어야 할 공지 수만 돌려준다(서버: 알림 먼저)."""
    from . import ocr
    cache = load_json(OCR_CACHE, {})
    todo, from_cache = [], 0
    for ev in events:
        need_fmt = not ev.get("_explicit") and not ev.get("_seg_formats")
        if ev["store"] in OCR_SKIP_STORES or ev.get("_gone") or ev.get("_pc") or not is_live(ev, now)[0]                 or re.search(r"//(?:x|twitter)\.com/", ev["url"]):   # X에만 있는 공지(트윗 링크)는 이미지 없음
            continue
        hit = cache.get(ev["url"])
        if hit and not hit.get("retried") and _ocr_mismatch(ev, hit):
            # 이미지에서 읽은 마감이 X·목록 마감과 하루 넘게 어긋남 → 잘못 읽은 것, 이미지를 더 받아 한 번 다시 읽기
            ev["_ocr_retry"] = True
            todo.append(ev)
            continue
        if ev.get("apply_end") and not need_fmt and ev["url"] in cache:
            # 응모 기간·형식은 이미 있어도 행사일·장소는 이미지에만 있을 수 있다(프롬스토어) → 저장된 결과로 채움
            apply_ocr(ev, _ocr_sched_from_cache(cache[ev["url"]]))
            from_cache += 1
            continue
        if hit and ("formats" not in hit or "last" not in hit or "public" not in hit):
            hit = None   # 형식 읽기 추가 전 결과 → 다시 읽는다
        if hit:
            ttl = 7 * 86400 if hit.get("apply_end") else 86400
            if time.time() - hit.get("_t", 0) < ttl:
                apply_ocr(ev, _ocr_sched_from_cache(hit))
                from_cache += 1
                continue
        todo.append(ev)
    # 행사가 가까운 것부터
    todo.sort(key=lambda e: e["event_date"] if isinstance(e.get("event_date"), date) else date(2100, 1, 1))
    todo = todo[:OCR_PER_RUN]
    if not todo or not allow_new:
        if from_cache:
            log(f"  이미지 일정: 저장된 결과 {from_cache}건 사용" + (f", 새로 읽을 공지 {len(todo)}건은 알림 뒤에" if todo else ""))
        return len(todo)

    def gather(ev):
        try:
            html = render(ev["url"], budget_ms=15000) if ev["store"] in RENDER_STORES else fetch(ev["url"], timeout=15, retries=1)
        except Exception:  # noqa: BLE001
            return ev, []
        paths = []
        for u in ocr.find_images(html, ev["url"])[:12]:
            p = ocr.download(u, referer=ev["url"])
            if p and p not in paths:
                paths.append(p)
            if len(paths) >= (12 if ev.get("_ocr_retry") else OCR_IMAGES_PER_EVENT):
                break
        return ev, paths

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        gathered = list(pool.map(gather, todo))
    all_paths = sorted({p for _ev, ps in gathered for p in ps})
    texts = dict(zip(all_paths, ocr.ocr_files(all_paths, timeout=60 + 8 * len(all_paths)))) if all_paths else {}
    found = 0
    for ev, ps in gathered:
        full = "\n".join(texts.get(p, "") for p in ps)
        evd = ev["event_date"] if isinstance(ev.get("event_date"), date) else None
        sched = ocr.extract_schedule(full, evd) if full else {}
        sched["formats"] = ocr_formats(full) if full else []
        flat = re.sub(r"\s+", " ", full)
        sched["last"] = bool(LAST_FANSIGN.search(flat))
        sched["public"] = bool(PUBLIC.search(flat))
        sched["venue"] = find_venue(flat)
        cache[ev["url"]] = {"_t": time.time(), "images": len(ps), **{k: iso(v) for k, v in sched.items()}}
        if ev.get("_ocr_retry"):
            cache[ev["url"]]["retried"] = True
        apply_ocr(ev, sched)
        if ev.get("schedule_src") == "image":
            found += 1
    cutoff = time.time() - 14 * 86400
    cache = {k: v for k, v in cache.items() if v.get("_t", 0) > cutoff}
    save_json(OCR_CACHE, cache)
    # 오래된 이미지 파일 정리
    try:
        for f in os.listdir(ocr.IMG_DIR):
            fp = os.path.join(ocr.IMG_DIR, f)
            if time.time() - os.path.getmtime(fp) > 7 * 86400:
                os.remove(fp)
    except OSError:
        pass
    log(f"  이미지 일정: 새로 {len(todo)}건 읽어 {found}건 찾음 (이미지 {len(all_paths)}장, "
        f"{time.time() - t0:.0f}초), 저장된 결과 {from_cache}건")


def sort_key(ev):
    d = ev.get("event_date") or date(2100, 1, 1)
    e = ev.get("apply_end")
    return (d if isinstance(d, date) else date(2100, 1, 1),
            e.timestamp() if isinstance(e, datetime) else 9e12, ev["artist"].lower())


def load_config() -> dict:
    cfg = load_json(CONFIG, {})
    cfg.setdefault("favorites", [])
    cfg.setdefault("telegram", {"token": "", "chat_id": "", "enabled": False})
    cfg.setdefault("notify", "favorites")      # favorites | all
    cfg.setdefault("include_overseas_alert", False)
    return cfg


def matches_favorite(ev: dict, favorites: list[str]) -> bool:
    """관심 아티스트 매칭. 영문 이름은 단어 단위(IVE가 DRIVE에 걸리지 않게), 한글은 띄어쓰기 무시."""
    text = f"{ev['artist']} {ev['title']}".lower()
    nospace = text.replace(" ", "")
    for f in favorites:
        f = f.strip().lower()
        if not f:
            continue
        if re.fullmatch(r"[a-z0-9 .&'!:\-]+", f):
            if re.search(rf"(?<![a-z0-9]){re.escape(f)}(?![a-z0-9])", text):
                return True
        elif f.replace(" ", "") in nospace:
            return True
    return False


# ───────────────────────── 실행 ─────────────────────────

def collect(notify: bool = True, pc_export: bool = False) -> dict:
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 15 * 60:
        log("다른 수집이 진행 중이라 건너뜀")
        return load_json(EVENTS, {})
    open(LOCK, "w").close()
    try:
        return _collect(notify, pc_export)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _check_bot():
    """서버: 알림 설정(Secrets)과 봇 구독자 수를 매번 로그에 남긴다 (값은 남기지 않음)."""
    token, url, key = (os.environ.get(k, "") for k in ("TELEGRAM_TOKEN", "SUBS_URL", "SUBS_KEY"))
    parts = [f"{k} {'있음' if v else '없음'}" for k, v in (("TELEGRAM_TOKEN", token), ("SUBS_KEY", key))]
    if url and key:
        try:
            import requests
            r = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=15)
            parts.append(f"봇 구독자 {len(r.json()['subscribers'])}명" if r.ok else f"봇 응답 {r.status_code}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"봇 확인 실패 {type(e).__name__}")
    if token:
        try:
            import requests
            me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
            parts.append(f"봇 @{me['result']['username']}" if me.get("ok") else "봇 토큰이 틀림")
        except Exception as e:  # noqa: BLE001
            parts.append(f"토큰 확인 실패 {type(e).__name__}")
    log("  알림 설정: " + ", ".join(parts))


HEALTH = os.path.join(DATA, "health.json")
REMIND = os.path.join(DATA, "remind.json")   # 마감·발표 알림 보낸 기록
HEALTH_RUNS = 3   # 판매처가 연속 몇 번 실패하면 관리자에게 알릴지


def _health(sources: list[dict]):
    """서버: 판매처가 연속 3번 실패하면 관리자에게 알리고, 다시 살아나면 한 번 더 알린다."""
    h = load_json(HEALTH, {})
    bad_now, fixed = [], []
    for st in sources:
        name = st["store"]
        rec = h.get(name, {"fails": 0, "alerted": False})
        failing = st["status"] in ("error", "blocked", "parse_error")
        if failing:
            rec["fails"] += 1
            rec["last"] = st.get("message", st["status"])
            if rec["fails"] >= HEALTH_RUNS and not rec["alerted"]:
                bad_now.append(f"• <b>{name}</b> {st['status']} — {html_escape(str(rec['last'])[:120])}")
                rec["alerted"] = True
        else:
            if rec.get("alerted"):
                fixed.append(name)
            rec = {"fails": 0, "alerted": False}
        h[name] = rec
    # PC가 올린 에버라인이 오래되면 (PC가 하루 넘게 꺼짐)
    pc = load_json(PC_EVERLINE, {})
    try:
        old = (now_kst() - datetime.fromisoformat(pc["checked_at"])).total_seconds() > 36 * 3600
    except (KeyError, ValueError, TypeError):
        old = True
    rec = h.get("_pc", {"alerted": False})
    if old and not rec["alerted"]:
        bad_now.append(f"• <b>에버라인</b> PC 수집이 36시간 넘게 없어요 (마지막 {pc.get('checked_at', '없음')}). PC를 켜 주세요.")
        rec["alerted"] = True
    elif not old and rec["alerted"]:
        fixed.append("에버라인(PC)")
        rec["alerted"] = False
    h["_pc"] = rec
    save_json(HEALTH, h)
    msg = []
    if bad_now:
        msg.append("⚠️ <b>팬싸 레이더 수집 이상</b>" + chr(10) + chr(10).join(bad_now))
    if fixed:
        msg.append("✅ 다시 정상: " + ", ".join(fixed))
    if msg:
        try:
            telegram.send_admin((chr(10) * 2).join(msg))
            log("  관리자 알림: " + ("이상 " + str(len(bad_now)) if bad_now else "") + (" 회복 " + str(len(fixed)) if fixed else ""))
        except Exception as e:  # noqa: BLE001
            log(f"  관리자 알림 실패: {e}")


def html_escape(s: str) -> str:
    import html
    return html.escape(s)


def _test_alert(live: list[dict]):
    """서버: 수동 실행 때 「테스트 알림」을 켜면, 실제 알림 형식 그대로 관리자에게 한 통."""
    opens = [e for e in live if e.get("status") == "open"] or live
    if not opens:
        log("  테스트 알림: 보낼 공지가 없음")
        return
    ev = {k: iso(v) for k, v in opens[0].items()}
    text = "🧪 <b>테스트 알림</b> (실제 새 공지가 아니에요)" + chr(10) * 2 + telegram.format_event(ev)
    try:
        log("  테스트 알림 보냄" if telegram.send_admin(text) else "  테스트 알림 못 보냄 (설정 없음)")
    except Exception as e:  # noqa: BLE001
        log(f"  테스트 알림 실패: {e}")


def _load_pc_events() -> tuple[list[dict], dict]:
    """PC가 올린 에버라인 공지 (data/pc_everline.json)."""
    data = load_json(PC_EVERLINE, {})
    evs = []
    for e in data.get("events", []):
        ev = restore_event(e)
        ev["_pc"] = True
        evs.append(ev)
    return evs, data


def _collect(notify: bool, pc_export: bool = False) -> dict:
    started = now_kst()
    if pc_export:
        todo = [s for s in SOURCES if s[0] in PC_ONLY_STORES]
        log(f"PC 수집 시작 ({', '.join(s[0] for s in todo)})")
    elif CLOUD:
        todo = [s for s in SOURCES if s[0] not in PC_ONLY_STORES]
        log("서버 수집 시작")
        _check_bot()
    else:
        todo = SOURCES
        log("수집 시작")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run_source, todo))

    sources, events = [], []
    for name, page, res, err, secs in results:
        st = {"store": name, "url": page, "seconds": round(secs, 1)}
        if err:
            st.update(status=err[0], message=err[1], count=0)
            log(f"  {name}: {err[0]} {err[1]}")
        else:
            built = [b for b in (build_event(name, r) for r in res.items) if b]
            events += built
            st.update(status="pending", found=res.found, fansign=len(built), empty_ok=res.empty_ok)
        sources.append(st)

    if CLOUD and not pc_export:
        pc_events, pc_data = _load_pc_events()
        for name, page, _fn in SOURCES:
            if name in PC_ONLY_STORES:
                mine = [e for e in pc_events if e.get("store") == name]
                st = {"store": name, "url": page, "seconds": 0, "status": "pc", "count": 0,
                      "message": f"PC에서 수집 ({pc_data.get('checked_at', '기록 없음')})"}
                events += mine
                sources.append(st)

    events = dedupe(events)
    now = now_kst()

    # 뮤직플랜트: 같은 아티스트·같은 날 행사 중 하나라도 '당첨자 발표'가 올라왔으면 나머지(ONLINE 등)도 응모 마감
    done = {(re.sub(r"\W", "", e["artist"].lower()), e.get("event_date")) for e in events
            if e["store"] == "뮤직플랜트" and e.get("winner_posted")}
    for e in events:
        if e["store"] == "뮤직플랜트" and not e.get("winner_posted") and \
                (re.sub(r"\W", "", e["artist"].lower()), e.get("event_date")) in done:
            e["winner_posted"] = True

    # 일시적으로 접속이 안 된 판매처는 직전 결과를 이어 쓴다 (사라졌다가 NEW로 다시 뜨는 것 방지)
    failed = {st["store"] for st in sources if (st["status"] in ("error", "blocked") and not pc_export)
              or (st["status"] == "pending" and st.get("found") == 0 and not st.get("empty_ok"))}
    if failed:
        prev = load_json(EVENTS, {}).get("events", [])
        carried = [restore_event(e) for e in prev if e.get("store") in failed]
        events += carried
        for st in sources:
            if st["store"] in failed:
                st["carried"] = sum(1 for e in carried if e["store"] == st["store"])
        if carried:
            log(f"  접속 실패 판매처 {', '.join(sorted(failed))}: 직전 결과 {len(carried)}건 유지")

    # 상세 페이지: 날짜가 아직 살아 있을 수 있는 것만
    cache = load_json(DETAIL_CACHE, {})
    targets = [ev for ev in events if not ev["_no_detail"] and is_live(ev, now)[0]]
    with ThreadPoolExecutor(max_workers=6) as pool:
        infos = list(pool.map(lambda ev: detail_info(ev["url"], cache, ev["_render_detail"]), targets))
    for ev, info in zip(targets, infos):
        apply_detail(ev, info)
    # 오래된 캐시 정리
    cutoff = time.time() - 3 * 86400
    cache = {k: v for k, v in cache.items() if v.get("_t", 0) > cutoff}
    save_json(DETAIL_CACHE, cache)

    # 응모 기간이 이미지에만 있는 공지 → 윈도우 OCR
    # 서버에서는 OCR 설치에 15분쯤 걸려서, 저장된 결과만 먼저 쓰고 새 이미지는 알림을 보낸 뒤에 읽는다
    pending_ocr = 0
    try:
        pending_ocr = ocr_pass(events, now, allow_new=not CLOUD)
    except Exception as e:  # noqa: BLE001
        log(f"  이미지 일정 읽기 실패: {e}")

    # 응모 기간을 X(트위터)에만 올리는 판매처 (점프업이엔티) → FxTwitter 공개 API
    try:
        from .xfeed import apply_x
        filled, extra = apply_x(events) if not pc_export else (0, [])
        added = 0
        for store, raw in extra:
            ev = build_event(store, raw)
            if not ev:
                continue
            fm = ocr_formats(raw["x_text"])
            if fm:
                ev["formats"] = fm
            ev["schedule_src"] = "x"
            ev["schedule_url"] = raw["url"]
            events.append(ev)
            added += 1
        if filled or added:
            log(f"  X 공지: 응모 기간 {filled}건 채움, X에만 있는 팬싸 {added}건 추가")
    except Exception as e:  # noqa: BLE001
        log(f"  X 공지 읽기 실패: {e}")

    live = []
    for ev in events:
        ok, status = is_live(ev, now)
        if ok and not ev.get("_gone"):
            ev["status"] = status
            live.append(ev)
    # 아티스트 이름 통일 (CHAERYEONG (채령) 형태)
    from .artist import unify
    unify(live)
    live.sort(key=sort_key)

    for st in sources:
        if st["status"] == "pc":
            st["count"] = sum(1 for e in live if e["store"] == st["store"])
            continue
        if st["status"] != "pending":
            continue
        n = sum(1 for e in live if e["store"] == st["store"])
        st["count"] = n
        if st["found"] == 0 and not st.get("empty_ok"):
            st["status"] = "parse_error"
            st["message"] = "목록 구조를 못 찾았어요 (사이트가 바뀌었을 수 있어요)"
        elif n == 0:
            st["status"] = "empty"
        else:
            st["status"] = "ok"

    if pc_export:
        # PC 모드: 에버라인 결과만 따로 저장 (알림·NEW 판별은 서버가 한다)
        live = [ev for ev in live if ev["store"] in PC_ONLY_STORES]
        payload = {"checked_at": iso(started), "sources": sources,
                   "events": [{k: iso(v) for k, v in ev.items() if not k.startswith("_")} for ev in live]}
        save_json(PC_EVERLINE, payload)
        stat = ", ".join(f"{st['store']} {st['status']}" for st in sources)
        log(f"PC 수집 끝: {stat}, 팬싸 {len(live)}건")
        return payload

    # 새 공지 판별
    seen = load_json(SEEN, None)
    first_run = seen is None
    seen = seen or {}
    # 첫 수집 때 이미 있던 공지는 'base'로 표시 (NEW 표시·알림 대상 아님)
    now_iso = started.isoformat(timespec="minutes")
    stamp = "base@" + now_iso if first_run else now_iso
    new_events = []
    for ev in live:
        if ev["id"] not in seen:
            # 처음 보는데 이미 응모 마감이면 '새 공지'가 아니다
            seen[ev["id"]] = "base@" + now_iso if ev["status"] == "closed" else stamp
            if not first_run and ev["status"] != "closed":   # 이미 마감된 공지는 알리지 않는다
                new_events.append(ev)
        ev["first_seen"] = seen[ev["id"]]
    # 60일 넘게 안 보인 id는 정리
    old = (started - timedelta(days=60)).isoformat()
    live_ids = {e["id"] for e in live}
    # 화면에서 잠시 빠져도(판매처 일시 오류 등) 60일 동안은 '본 적 있음'으로 기억한다
    seen = {k: v for k, v in seen.items() if k in live_ids or v.split("@")[-1] >= old}
    save_json(SEEN, seen)

    if notify and new_events:
        try:
            cfg = load_config()
            sent = telegram.notify_new(new_events, cfg)
            if sent:
                log(f"텔레그램 알림 {sent}건 보냄")
        except Exception as e:  # noqa: BLE001
            log(f"텔레그램 알림 실패: {e}")
        # 봇 구독자별 알림 (서버 수집)
        token, subs_url, subs_key = (os.environ.get(k, "") for k in ("TELEGRAM_TOKEN", "SUBS_URL", "SUBS_KEY"))
        if token and subs_url and subs_key:
            try:
                people, msgs = telegram.notify_subscribers(new_events, token, subs_url, subs_key)
                log(f"구독자 알림: {people}명에게 {msgs}통")
            except Exception as e:  # noqa: BLE001
                log(f"구독자 알림 실패: {e}")
        elif CLOUD:
            missing = [k for k, v in (("TELEGRAM_TOKEN", token), ("SUBS_URL", subs_url), ("SUBS_KEY", subs_key)) if not v]
            log(f"구독자 알림 못 보냄: GitHub Secrets에 {', '.join(missing)} 없음")

    # 봇 구독자에게 응모 마감·당첨자 발표 알림 (서버 수집만, 보낸 기록은 data/remind.json)
    if notify and CLOUD:
        token, subs_url, subs_key = (os.environ.get(k, "") for k in ("TELEGRAM_TOKEN", "SUBS_URL", "SUBS_KEY"))
        if token and subs_url and subs_key:
            try:
                rsent = load_json(REMIND, {})
                people, n = telegram.send_reminders(live, now_kst(), rsent, token, subs_url, subs_key)
                cutoff = (now_kst() - timedelta(days=14)).isoformat()
                save_json(REMIND, {k: v for k, v in rsent.items() if v >= cutoff})
                if n:
                    log(f"마감·발표 알림: 공지 {n}건, {people}명에게")
            except Exception as e:  # noqa: BLE001
                log(f"마감·발표 알림 실패: {e}")

    if CLOUD:
        _health(sources)
        if os.environ.get("TEST_ALERT") == "1":
            _test_alert(live)

    # 서버: 알림을 보낸 뒤 새 공지 이미지 읽기 (OCR 설치 포함) → 응모 기간 채워서 저장
    if pending_ocr:
        try:
            ocr_pass(live, now)
            for ev in live:
                ok, status = is_live(ev, now)
                ev["status"] = status if ok else "_drop"
            live = [ev for ev in live if ev["status"] != "_drop"]
            live.sort(key=sort_key)
            for st in sources:
                if st.get("count") is not None and st["status"] in ("ok", "empty", "pc"):
                    st["count"] = sum(1 for e in live if e["store"] == st["store"])
        except Exception as e:  # noqa: BLE001
            log(f"  이미지 일정 읽기 실패: {e}")

    out_events = []
    for ev in live:
        out_events.append({k: iso(v) for k, v in ev.items() if not k.startswith("_")})
    payload = {
        "checked_at": iso(started),
        "took_seconds": round((now_kst() - started).total_seconds(), 1),
        "events": out_events,
        "sources": sources,
    }
    save_json(EVENTS, payload)
    log(f"수집 끝: 팬싸 {len(live)}건, 새 공지 {len(new_events)}건"
        f"{' (첫 수집이라 알림 없이 기록만)' if first_run else ''}, {payload['took_seconds']}초")

    return payload


def main():
    try:
        collect(notify="--no-notify" not in sys.argv, pc_export="--pc" in sys.argv)
    except Exception:  # noqa: BLE001
        log("수집 중 오류:\n" + traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
