"""공지 이미지에서 일정 읽기 — 윈도우 기본 OCR(한국어, 무료·오프라인) 사용.

흐름: 상세 페이지의 본문 이미지 찾기 → 내려받기 → ocr.ps1로 글자 읽기 → 날짜 모양으로 응모 기간·발표·행사일 추정.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from requests.utils import requote_uri

from .common import KST, ROOT, _session, nearest_year, soup, today_kst

PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr.ps1")
IMG_DIR = os.path.join(ROOT, "data", "ocr_img")

# 본문이 아닌 이미지(로고·버튼·배너·아이콘 등)
SKIP_IMG = re.compile(r"logo|icon|btn|button|banner|bnr|/skin|/layout|/design/|/_wg/|common|sns|kakao(?!talk_\d)|naver|"
                      r"instagram|facebook|twitter|youtube|arrow|top_|footer|header|lang_|\.gif|\.svg|"
                      r"data:image|blank|spacer|loading|placeholder", re.I)


CONTENT_HINT = re.compile(r"cont|detail|board|view|bd-cont|img_wrapper|additional|editor|article|post|desc|"
                          r"WritenBy|fr-view|se-main|product-detail|event", re.I)
GOOD_SRC = re.compile(r"NNEditor|editor|attach|/upload/(?!.*(?:jwgimg|icon|btn|logo))|/web/product 2/|jpg3\.kr/EVENT|"
                      r"/EVENT/|/event/|shopimages|cdn\.shopify|/files/|cloudfront|cafe24img|data:image", re.I)


def find_images(html: str, base: str) -> list[str]:
    """상세 페이지 본문 이미지 후보. 본문 영역·에디터 업로드 이미지를 앞에 둔다."""
    s = soup(html)
    for t in s(["header", "footer", "nav", "script", "style", "noscript"]):
        t.decompose()
    scored = []
    for n, img in enumerate(s.find_all("img")):
        src = (img.get("ec-data-src") or img.get("data-original") or img.get("data-src")
               or img.get("data-lazy") or img.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("data:image"):
            if len(src) < 40000:   # 작은 인라인 그림은 아이콘
                continue
            url = src
        else:
            if SKIP_IMG.search(src) or re.search(r"/product/(?:small|tiny|medium|extra)/", src):
                continue
            url = requote_uri(urljoin(base, src))
            if not url.startswith("http"):
                continue
        w = img.get("width") or ""
        if w.isdigit() and int(w) < 200:
            continue
        score = 0
        if GOOD_SRC.search(src):
            score += 2
        parents = " ".join(str(p.get("class") or "") + " " + str(p.get("id") or "") for p in list(img.parents)[:6])
        if CONTENT_HINT.search(parents):
            score += 1
        if re.search(r"/product/big/|thumbnail|keyImg", src + parents):
            score -= 2     # 앨범 커버(상품 대표 사진)
        if url not in (u for _s, _n, u in scored):
            scored.append((score, n, url))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [u for sc, _n, u in scored if sc >= 0]


def download(url: str, referer: str = "", min_bytes: int = 12000) -> str | None:
    """이미지를 받아 data/ocr_img에 저장. 작거나(아이콘) 너무 작은 이미지는 버린다."""
    os.makedirs(IMG_DIR, exist_ok=True)
    name = hashlib.sha1(url[:5000].encode()).hexdigest()[:16]
    for ext in (".jpg", ".png"):
        p = os.path.join(IMG_DIR, name + ext)
        if os.path.exists(p):
            return p
    try:
        if url.startswith("data:image"):
            data = base64.b64decode(url.split(",", 1)[1])
            ctype = url[5:url.find(";")]
        else:
            r = _session.get(url, timeout=25, headers={"Referer": requote_uri(referer)} if referer else None)
            if not r.ok:
                return None
            data, ctype = r.content, r.headers.get("Content-Type", "")
        if len(data) < min_bytes:
            return None
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.width < 400 or im.height < 300:
            return None
        if im.format not in ("JPEG", "PNG"):
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=92)
            data = buf.getvalue()
        ext = ".png" if im.format == "PNG" else ".jpg"
        p = os.path.join(IMG_DIR, name + ext)
        with open(p, "wb") as f:
            f.write(data)
        return p
    except Exception:  # noqa: BLE001
        return None


_OCR_READY = False


def ensure_korean_ocr():
    """서버(GitHub Actions)에서만: 한국어 OCR이 없으면 그때 설치한다(약 11분). 새로 읽을 이미지가 있을 때만 불린다."""
    global _OCR_READY
    if _OCR_READY or os.environ.get("FANSIGN_OCR_AUTOINSTALL") != "1":
        return
    ps = ("$c = Get-WindowsCapability -Online -Name 'Language.OCR~~~ko-KR*'; "
          "if ($c.State -ne 'Installed') { Add-WindowsCapability -Online -Name $c.Name | Out-Null; 'installed' } else { 'ready' }")
    import time
    t0 = time.time()
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=1800)
    from .collect import log
    log(f"  한국어 OCR: {r.stdout.decode('utf-8', 'ignore').strip() or r.stderr.decode('utf-8', 'ignore')[:200]} ({time.time() - t0:.0f}초)")
    _OCR_READY = True


def ocr_files(paths: list[str], timeout: int = 600) -> list[str]:
    """여러 이미지를 한 번에 OCR. 이미지마다 글자 묶음 하나."""
    if not paths:
        return []
    ensure_korean_ocr()
    flags = 0x08000000 if os.name == "nt" else 0
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS1, *paths],
                       capture_output=True, timeout=timeout, creationflags=flags)
    text = r.stdout.decode("utf-8", errors="ignore")
    parts = text.split("@@END@@")
    out = []
    for i in range(len(paths)):
        chunk = parts[i] if i < len(parts) else ""
        out.append("" if "@@ERROR@@" in chunk else chunk.strip())
    return out


# ───────────────────────── 글자 → 일정 ─────────────────────────

_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def normalize(t: str) -> str:
    """OCR이 숫자를 글자로 읽은 것 바로잡기 (11:OOAM → 11:00AM, 2O26 → 2026)."""
    t = re.sub(r"(?<=[\d:.])[Oo]|[Oo](?=\d)|(?<=\d)[Oo]", "0", t)
    t = re.sub(r"(?<=[\d:.])[Oo]", "0", t)  # 연달은 O 처리
    t = re.sub(r"(?<=\d)[lI|](?=\d)|(?<=[.:])[lI|](?=\d)|(?<=\d)[lI|](?=[.:])", "1", t)
    # "2026.112414:00" → "2026.11.24 14:00", "2026.1127" → "2026.11.27"
    t = re.sub(r"(20\d{2})\s*\.\s*(\d{2})(\d{2})\s*(\d{1,2}:\d{2})", r"\1.\2.\3 \4", t)
    t = re.sub(r"(20\d{2})\s*\.\s*(\d{2})(\d{2})(?!\d)", r"\1.\2.\3", t)
    t = re.sub(r"(20\d{2}\.\d{2}\.\d{2})(\d{1,2}:\d{2})", r"\1 \2", t)   # "2027.01.0918:30" → "2027.01.09 18:30"
    t = re.sub(r"[(\[]?\s*(?:KST|K5T|KS7|CKST|한국\s*시간)\s*[)\]]?", " ", t)  # "18:00 (KST) ~" → 범위가 이어지게
    t = re.sub(r"(년\s*)[^\d\s](\d)\s*월", r"\g<1>1\2월", t)               # "2026년 햅1월" → "11월" (1이 깨짐)
    t = re.sub(r"(?<=\d)[가-힣](?=:\d)", "", t)                             # "1뛉:00" → "1:00" (날짜만이라도)
    t = t.replace("：", ":").replace("～", "~").replace("∼", "~").replace("〜", "~")
    # 시각의 콜론을 엉뚱한 글자로 읽은 것: 18羽0, 17•00, 12℃OPM, 15100
    t = t.replace("℃", ":0")
    t = re.sub(r"(\d{1,2})\s?[\u4e00-\u9fff•·¦]\s?(\d{1,2})(?=\s|[(（]|AM|PM|am|pm|$)", r"\1:\2", t, flags=re.M)
    t = re.sub(r"(?<!\d)(\d) (\d)\s*:\s*(\d{2})", r"\1\2:\3", t)           # "1 2:00" → "12:00"
    t = re.sub(r"(?<=[\d:])[Oo]", "0", t)                                  # ℃ 처리 뒤 남은 O
    t = re.sub(r"(?<!\d)(1)\s+([0-2])\s*월", r"\1\2월", t)                 # "1 1월", "1 1 월" → "11월"
    t = re.sub(r"(?<![\d.])(2\d)\.(\d{1,2})\.(\d{1,2})(?![\d.])", r"20\1.\2.\3", t)   # "26.11.16" → "2026.11.16"
    t = re.sub(r"(?<![\d.])(2\d)(1[0-2]|0[1-9])\.(\d{2})(?![\d.])", r"20\1.\2.\3", t)  # "2611.27" → "2026.11.27"
    t = re.sub(r":9(?!\d)", ":59", t)
    t = re.sub(r"(?<![\d:.])(\d{1,2})(\d{2})\s*(AM|PM|am|pm)", r"\1:\2 \3", t)            # "300 PM" → "3:00 PM"
    t = re.sub(r"(\d)\.\s*:", r"\1:", t)                                                  # "15.:00" → "15:00"
    t = re.sub(r"([)）』\]]\s*)(\d{1,2})\.(\d{2})(?!\d)", r"\1\2:\3", t)                  # ") 17.00" → ") 17:00"
    t = re.sub(r"(년\s*)1[^\d\s]\s*월", r"\g<1>11월", t)                                  # "1뛉월" → "11월"
    t = re.sub(r"([)）』\]]\s*)([01]\d|2[0-3])1([0-5]\d)(?!\d)", r"\1\2:\3", t)           # ") 23159" → "23:59"
    t = t.replace("』", ")").replace("『", "(")
    t = re.sub(r"(?<![A-Za-z])([Il|]{1,2})(?=\s*월)", lambda m: "1" * len(m.group(1)), t)   # "II월" → "11월"
    t = re.sub(r"(?<![A-Za-z])[Il|](\d)(?=\s*월)", r"1\1", t)                              # "I2월" → "12월"
    # 영어 날짜 "November 1 0, 2026" / "Nov 10, 2026" → "2026.11.10"
    def _en(m):
        mo = _MONTHS[m.group(1)[:3].lower()]
        day = (m.group(2) + (m.group(3) or "")).strip()
        return f"{m.group(4)}.{mo}.{day}"
    t = re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d)\s?(\d)?\s*,\s*(20\d{2})", _en, t, flags=re.I)                                       # "23巧9" → 23:59 (5가 콜론과 뭉개짐)
    t = re.sub(r"(20\d{2})(\d{2})\.(\d{2})", r"\1.\2.\3", t)                # "202611.23" → "2026.11.23"
    t = re.sub(r"([)）]\s*)([01]\d|2[0-3])[1lI|]?([0-5]\d)(?=\s|[(（~\-–]|$)", r"\1\2:\3", t, flags=re.M)  # ") 1400", ") 15100"
    return t


DT = (r"(?:(20\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?"
      r"(?:\s*[(\[]\s*[월화수목금토일요a-zA-Z]{1,4}\s*[)\]]|\s*(?:MON|TUE|WED|THU|FRI|SAT|SUN)\b)?"
      r"(?:\s*(오전|오후|AM|PM|am|pm)?\s*(\d{1,2})\s*(?:[:;]\s*(\d{1,2})|시(?:\s*(\d{1,2})\s*분)?|(?=\s*(?:AM|PM|am|pm)))\s*(AM|PM|am|pm)?)?")
RANGE = re.compile(DT + r"\s*(?:~|-|–|—|부터)\s*" + DT)
SINGLE = re.compile(DT)


def _mk(g, end=False, ref_year=None):
    y, mo, d, ap1, h, mi, mi2, ap2 = g
    try:
        mo, d = int(mo), int(d)
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        if y:
            year = int(y)
        elif ref_year:
            year = ref_year
        else:
            year = nearest_year(mo, d).year
        if h:
            hour, minute = int(h), int(mi or mi2 or 0)
            ap = (ap1 or ap2 or "").upper()
            if ap in ("오후", "PM") and hour < 12:
                hour += 12
            if ap in ("오전", "AM") and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                hour, minute = 23, 59
        else:
            hour, minute = (23, 59) if end else (0, 0)
        return datetime(year, mo, d, hour, minute, tzinfo=KST)
    except (ValueError, TypeError, AttributeError):
        return None


def _plausible(dt: datetime | None) -> bool:
    if not dt:
        return False
    today = today_kst()
    return today - timedelta(days=400) <= dt.date() <= today + timedelta(days=400)


def extract_schedule(text: str, event_date: date | None = None) -> dict:
    """OCR 글자에서 응모 기간 / 당첨 발표 / 행사일 추정."""
    t = normalize(text)
    flat = " ".join(t.split())
    ranges, used = [], []
    for m in RANGE.finditer(flat):
        s = _mk(m.groups()[:8])
        e = _mk(m.groups()[8:], end=True, ref_year=(s.year if s and not m.group(9) else None))
        if s and e and e < s and not m.group(9):
            e = e.replace(year=e.year + 1)
        if _plausible(s) and _plausible(e) and timedelta(0) <= e - s <= timedelta(days=120):
            ctx = flat[max(0, m.start() - 40): m.start()]
            ranges.append((s, e, ctx))
            used.append((m.start(), m.end()))
    singles = []
    for m in SINGLE.finditer(flat):
        if any(a <= m.start() < b for a, b in used):
            continue
        dt = _mk(m.groups())
        # 연도나 시각이 붙은 날짜만 (앨범명·숫자 오인식 줄이기)
        if dt and (m.group(1) or m.group(5)) and _plausible(dt):
            singles.append((dt, flat[max(0, m.start() - 40): m.start()]))

    out: dict = {}
    # 응모 기간: '응모/구매/판매/이벤트 기간' 문맥 우선, 없으면 행사일보다 앞서 끝나는 첫 기간
    ev = event_date
    if not ev:
        cand = [d for d, c in singles if re.search(r"일정|일시|사인회|행사|팬싸|진행", c)]
        if cand:
            ev = max(cand).date()
    pick = None
    for s, e, c in ranges:
        if re.search(r"응모|구매|판매|이벤트\s*기간|기간", c):
            pick = (s, e)
            break
    if not pick:
        for s, e, c in ranges:
            if not ev or e.date() <= ev:
                pick = (s, e)
                break
    if pick:
        out["apply_start"], out["apply_end"] = pick
    # 당첨 발표 / 행사일: 응모 마감 뒤의 단일 날짜들 중에서
    after = sorted({d for d, _c in singles if not pick or d > pick[1]})
    if ev:
        before_ev = [d for d in after if d.date() < ev]
        if pick and before_ev:
            out["winner"] = before_ev[0]
        if not event_date:
            out["event_date"] = ev      # '사인회 일정/일시' 문맥에서 찾은 행사일
    elif after:
        if len(after) >= 2 and pick:
            out["winner"] = after[0]
        if not event_date:
            out["event_date"] = after[-1].date()
    return out
