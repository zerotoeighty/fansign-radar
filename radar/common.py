"""공통 도구: 가져오기, 크롬 렌더링, 날짜 해석, 팬싸 판별, 아티스트 이름 추출."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"})


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


# ───────────────────────── 가져오기 ─────────────────────────

class Blocked(Exception):
    """대기열(NetFunnel) 등으로 실제 내용을 못 받음."""


def fetch(url: str, timeout: int = 20, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 429 and i < retries:
                time.sleep(3 + i * 3)
                continue
            r.raise_for_status()
            if r.encoding in (None, "ISO-8859-1"):
                r.encoding = r.apparent_encoding
            return r.text
        except requests.RequestException as e:  # noqa: PERF203
            last = e
            time.sleep(1.5)
    raise last  # type: ignore[misc]


def fetch_json(url: str, timeout: int = 20):
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def chrome_path() -> str | None:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


_chrome_slots = threading.Semaphore(2)


def render(url: str, budget_ms: int = 15000, timeout: int = 75) -> str:
    """화면 없는 크롬으로 페이지를 열어 자바스크립트 실행 뒤의 HTML을 돌려준다.

    접속 대기열(NetFunnel)도 일반 방문자처럼 기다렸다가 통과한다.
    렌더링마다 임시 프로필을 따로 써서 동시에 여러 개를 열어도 서로 막지 않는다.
    """
    exe = chrome_path()
    if not exe:
        raise RuntimeError("크롬을 찾지 못했어요")
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    with _chrome_slots:
        profile = tempfile.mkdtemp(prefix="fansign_chrome_")
        try:
            with tempfile.TemporaryFile() as out:
                try:
                    subprocess.run(
                        [exe, "--headless=new", "--disable-gpu", "--no-first-run",
                         "--disable-extensions", "--mute-audio", f"--user-data-dir={profile}",
                         f"--virtual-time-budget={budget_ms}", "--dump-dom", url],
                        stdout=out, stderr=subprocess.DEVNULL, timeout=timeout, creationflags=flags,
                    )
                except subprocess.TimeoutExpired as e:
                    raise RuntimeError("크롬 렌더링 시간 초과") from e
                out.seek(0)
                return out.read().decode("utf-8", errors="ignore")
        finally:
            shutil.rmtree(profile, ignore_errors=True)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text_of(node) -> str:
    return " ".join(node.get_text(" ").split())


def page_text(html: str) -> str:
    s = soup(html)
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    return text_of(s)


def row_text(a) -> str:
    """링크가 들어 있는 한 줄(행/카드)의 글자. 옆 공지 글자가 섞이지 않게 좁게 잡는다."""
    base = text_of(a)
    node = a
    best = base
    for _ in range(5):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "[document]"):
            break
        hrefs = {x.get("href") for x in parent.find_all("a", href=True)}
        hrefs.discard(None)
        t = text_of(parent)
        if len(hrefs - {a.get("href"), "#", "javascript:;", "javascript:void(0);"}) > 2:
            break
        if len(t) > len(base) + 260:
            break
        best = t
        node = parent
    return best


def clean_title(t: str) -> str:
    t = re.sub(r"^\s*(상품명|Product Name)\s*[:：]\s*", "", t, flags=re.I)
    t = t.replace("\u200b", "").replace("\ufeff", "")
    t = " ".join(t.split())
    # "제목 제목" 처럼 두 번 반복된 글자는 하나로
    half = len(t) // 2
    if len(t) > 20:
        for cut in range(half - 2, half + 3):
            a, b = t[:cut].strip(), t[cut:].strip()
            if a and a == b:
                return a
    return t


# ───────────────────────── 날짜 ─────────────────────────

def nearest_year(month: int, day: int, ref: date | None = None) -> date | None:
    """연도 없는 월/일의 연도를 정한다.

    팬싸 공지는 보통 행사 2~3달 전에 올라오므로, 120일 안쪽의 앞날이면 올해/내년의 앞날로,
    그보다 멀면 지난 날짜로 본다. (11월 기준 2/21 → 내년 2월, 5/21 → 지난 5월)
    """
    ref = ref or today_kst()
    cands = []
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            cands.append(date(y, month, day))
        except ValueError:
            continue
    if not cands:
        return None
    future = [d for d in cands if 0 <= (d - ref).days <= 120]
    if future:
        return min(future)
    past = [d for d in cands if d < ref]
    return max(past) if past else min(cands)


def title_event_date(title: str) -> date | None:
    """제목에 적힌 행사 날짜. [261129] (261207) [1129] 1226 11/29 12/05 형태."""
    m = re.search(r"[\[(（【]\s*(2\d)(0[1-9]|1[0-2])([0-3]\d)(?!\d)", title)
    if m:
        try:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(?<![\d:/.])(1[0-2]|0?[1-9])\s*/\s*([0-3]?\d)(?![\d/])", title)
    if m and 1 <= int(m.group(2)) <= 31:
        return nearest_year(int(m.group(1)), int(m.group(2)))
    m = re.search(r"(?:^|[\[\]\s(（【])(0[1-9]|1[0-2])([0-3]\d)(?=[\s\]\)）】\-~]|$)", title)
    if m and 1 <= int(m.group(2)) <= 31:
        return nearest_year(int(m.group(1)), int(m.group(2)))
    return None


_DT = (r"(?:(20\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?"
       r"(?:\s*\([^)]{0,6}\))?(?:\s*(오전|오후|AM|PM)?\s*(\d{1,2})\s*(?::\s*(\d{2})|시)?)?")


def _mk(y, mo, d, ampm, h, mi, end: bool, ref_year: int | None = None) -> datetime | None:
    try:
        year = int(y) if y else None
        mo, d = int(mo), int(d)
        if year is None:
            if ref_year:
                year = ref_year
            else:
                nd = nearest_year(mo, d)
                year = nd.year if nd else today_kst().year
        if h is not None and h != "":
            hour = int(h)
            if ampm in ("오후", "PM") and hour < 12:
                hour += 12
            if ampm in ("오전", "AM") and hour == 12:
                hour = 0
            minute = int(mi) if mi else 0
            if hour >= 24:
                hour, minute = 23, 59
        else:
            hour, minute = (23, 59) if end else (0, 0)
        return datetime(year, mo, d, hour, minute, tzinfo=KST)
    except (ValueError, TypeError):
        return None


def parse_period(text: str) -> tuple[datetime | None, datetime | None]:
    """'2026. 11. 22 ~ 2026. 11. 25' / '2026-10-19 ~ 2026-12-05' / '11.24(월) 15:00 ~ 11.27(목) 23:59'."""
    m = re.search(_DT + r"\s*(?:~|∼|～|부터|-|–)\s*" + _DT, text)
    if not m:
        return None, None
    g = m.groups()
    start = _mk(*g[0:6], end=False)
    end = _mk(*g[6:12], end=True, ref_year=(start.year if start and not g[6] else None))
    if start and end and end < start and not g[6]:
        end = end.replace(year=end.year + 1)
    return start, end


def labelled_period(text: str, label: str) -> tuple[datetime | None, datetime | None]:
    m = re.search(rf"(?:{label})\s*(?:기간|일시|날짜|마감)?\s*[:：]?\s*", text)
    if not m:
        return None, None
    return parse_period(text[m.end(): m.end() + 120])


def labelled_datetime(text: str, label: str) -> datetime | None:
    m = re.search(rf"(?:{label})\s*(?:일시|날짜|일정)?\s*[:：]?\s*" + _DT, text)
    if not m:
        return None
    g = m.groups()[-6:]
    return _mk(*g, end=False)


def posted_date(text: str) -> date | None:
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


# ───────────────────────── 팬싸 판별 ─────────────────────────

CALL = re.compile(
    r"영상\s*통화|영통|video\s*call|meet\s*(?:&|and|＆)\s*(?:diy\s*&\s*)?call|photo\s*&\s*call|"
    r"\bonline\b(?!\s*(?:shop|store|mall))|온라인\s*(?:팬\s*사인|사인)", re.I)
MEET = re.compile(
    r"대면|오프라인|\boffline\b|fan\s*-?\s*sign|팬\s*사인|팬싸|사인회|\bsigning\b|하이\s*터치|hi-?touch", re.I)
EXCLUDE = re.compile(
    r"당첨\s*자?\s*(?:발표|공지|안내)|당첨\s*안내|\bwinners?\b|럭키\s*드로우|lucky\s*draw|"
    r"^\s*\[\s*공지\s*\]|교환\s*(?:신청|관련)|일정\s*(?:변경|취소)\s*안내|취소\s*안내", re.I)
OVERSEAS = re.compile(
    r"\bin\s+(?:tokyo|osaka|japan|hong\s*kong|hongkong|taipei|kaohsiung|bangkok|singapore|la|"
    r"shanghai|beijing|manila|jakarta)|japan\s*(?:fan\s*)?event|japn|일본|해외|香港|台北|\btokyo\b|"
    r"\bosaka\b|kaohsiung|hong\s*kong|taipei", re.I)
PUBLIC = re.compile(r"(?<!비)(?<!비\s)공개\s*(?:팬\s*)?사인회|(?<!비)공개\s*팬싸|open\s*fan\s*sign", re.I)
VENUE = re.compile(
    r"(영등포\s*타임스퀘어|타임스퀘어|스타필드(?:\s*[가-힣]+)?|코엑스(?:몰)?|\bIFC\s*몰?|아이에프씨\s*몰|"
    r"(?:용산\s*)?아이파크\s*몰|롯데\s*월드\s*몰|롯데\s*몰(?:\s*[가-힣]+점)?|더\s*현대(?:\s*서울)?|여의도\s*더현대|"
    r"(?:롯데|현대|신세계|AK|갤러리아)\s*(?:백화점|플라자)(?:\s*[가-힣]+점)?|(?:롯데|현대|신세계)\s*[가-힣]+점|"
    r"영풍\s*문고(?:\s*[가-힣]+점)?|교보\s*문고(?:\s*[가-힣]+점)?|핫트랙스(?:\s*[가-힣]+점)?|"
    r"COEX|STARFIELD|TIMES\s*SQUARE|HYUNDAI\s*SEOUL)", re.I)
PLACE_CUE = re.compile(r"(?:행사|진행|사인회|이벤트)?\s*장소\s*[:：]?|행사장|venue|location|place", re.I)


def classify(title: str, hint: str = "") -> dict | None:
    """팬싸가 아니면 None. 맞으면 {'call','meet','overseas','public'}."""
    t = f"{title} {hint}"
    if EXCLUDE.search(title):
        return None
    has_call = bool(CALL.search(t))
    # "1:1 영상통화 팬사인회", "MEET&DIY&CALL FAN SIGN EVENT"의 '팬사인회'는 영상통화 행사 이름이므로 대면으로 치지 않는다
    after_call = re.sub(rf"(?:{CALL.pattern})\s*(?:팬\s*사인회|사인회|fan\s*-?\s*sign(?:ing)?)?", " ", t, flags=re.I)
    has_meet = bool(MEET.search(after_call))
    # 뮤직플랜트 "[11/29 ONLINE] … 팬사인회" = 영상통화 팬사인회
    if re.search(r"\[[\d/\s]*online", title, re.I) and not re.search(r"대면|offline|오프라인", title, re.I):
        has_meet = False
    if not (has_call or has_meet):
        return None
    return {
        "call": has_call,
        "meet": has_meet,
        "overseas": bool(OVERSEAS.search(t)),
        "public": bool(PUBLIC.search(t)),
        # 제목에 대면/영상통화가 직접 적혀 있는지 (없으면 상세·이미지로 형식을 보충한다)
        "explicit": bool(re.search(r"(?<!비)대면|오프라인|\boffline\b", t, re.I)) or has_call,
    }


def find_venue(text: str) -> str | None:
    for m in VENUE.finditer(text):
        near = text[max(0, m.start() - 80): m.end() + 40]
        if PLACE_CUE.search(near):
            return m.group(1).strip()
    return None


# ───────────────────────── 아티스트 ─────────────────────────

_LEAD = re.compile(
    r"^\s*(?:\[[^\]]*\]|【[^】]*】|<[^>]*>|\((?:\d[^)]*|[^)]*(?:EVENT|이벤트|온라인|오프라인)[^)]*)\)|"
    r"\d{4}(?=\s)|\d{1,2}/\d{1,2}(?=\s)|EVERLINE|위드뮤|뮤직아트)\s*", re.I)
_CUTS = [
    r"\s[-–—]\s", r"\s?\[", r"\s?‘", r"\s?'", r"\s?<", r"\s?\"", r"\s?“", r"\s?「",
    r"\s(?:the\s)?\d+\s*(?:st|nd|rd|th)\b", r"\s(?:미니|정규|싱글|디지털|스페셜|단체|1:1|첫)",
    r"\s(?:mini|single|album|special|digital|repackage|season'?s|seasons)\b",
    r"\sEP\b", r"\s20\d{2}\s", r"\s발매", r"\s대면", r"\s영상", r"\s팬\s?사인", r"\s사인회",
    r"\s럭키", r"\s애플뮤직", r"\s시즌", r"\s예판", r"\s(?:video|meet|fan\s?sign|offline|online|photo)\b",
    r"\s\d+(?:집|th)\b", r"\s이벤트", r"\s팬\s?이벤트", r"/",
]
_CUT_RE = re.compile("|".join(_CUTS), re.I)


def artist_from(title: str) -> str:
    t = clean_title(title)
    for _ in range(6):
        n = _LEAD.sub("", t, count=1)
        if n == t:
            break
        t = n
    m = _CUT_RE.search(t, 1)
    a = t[: m.start()] if m else t
    a = a.strip(" -–—:|·,")
    # "TWS(투어스)"처럼 괄호가 열린 채 끊겼으면 닫아 준다
    if a.count("(") > a.count(")"):
        close = t.find(")", len(a))
        if close != -1 and close - len(a) < 25:
            a = t[: close + 1].strip()
    # "이브 (Yves) Cute & Anxiety" → "이브 (Yves)"
    m2 = re.match(r"^(.+?\s?\([^()]*\))\s+\S", a)
    if m2 and len(m2.group(1)) >= 3:
        a = m2.group(1)
    return a[:40] if len(a) >= 1 else clean_title(title)[:30]
