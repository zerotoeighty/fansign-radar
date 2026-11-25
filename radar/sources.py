"""판매처별 수집기.

각 수집기는 Result를 돌려준다.
  items     : 목록에서 찾은 공지 (팬싸가 아닌 것도 섞여 있음 — 판별은 collect.py)
  found     : 목록 구조가 제대로 잡혔는지 확인하는 숫자(공지 링크 개수)
  empty_ok  : 공지 링크가 0개여도 '정상 빈 목록'으로 볼 표시를 찾았는지
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

from .common import (KST, Blocked, clean_title, fetch, fetch_json, parse_period, posted_date,
                     render, row_text, soup, text_of)


@dataclass
class Result:
    items: list[dict] = field(default_factory=list)
    found: int = 0
    empty_ok: bool = False
    note: str = ""


def _anchors(html: str, base: str, href_re: str):
    s = soup(html)
    for a in s.find_all("a", href=True):
        if re.search(href_re, a["href"]):
            yield a, urljoin(base, a["href"].replace("&amp;", "&"))


def _board(html: str, base: str, href_re: str, strip_tail: str | None = None) -> Result:
    """게시판형: 링크 하나 = 공지 하나. 같은 행의 작성일을 함께 읽는다."""
    res = Result()
    seen: dict[str, dict] = {}
    for a, url in _anchors(html, base, href_re):
        title = clean_title(text_of(a))
        if strip_tail:
            title = re.sub(strip_tail, "", title).strip()
        if len(title) < 4:
            continue
        res.found += 1
        if url in seen and len(seen[url]["title"]) >= len(title):
            continue
        row = row_text(a)
        seen[url] = {"title": title, "url": url, "row": row, "posted": posted_date(row.replace(title, " "))}
    res.items = list(seen.values())
    return res


def _products(html: str, base: str, href_re: str) -> Result:
    """쇼핑몰 상품형(카페24 등): 같은 상품 링크가 이미지·이름으로 여러 번 나온다."""
    res = Result()
    best: dict[str, str] = {}
    for a, url in _anchors(html, base, href_re):
        raw = text_of(a)
        alt = " ".join(i.get("alt", "") for i in a.find_all("img")).strip()
        cand = clean_title(raw) or clean_title(alt)
        if len(cand) < 4:
            continue
        key = re.sub(r"[&?](?:cate_no|display_group)=\d+", "", url).rstrip("/")
        key = re.sub(r"/category/\d+/display/\d+$", "", key)
        prefer = bool(re.match(r"\s*(상품명|Product Name)", raw, re.I))
        cur = best.get(key)
        if cur is None or prefer or (len(cand) < len(cur) and not cur.startswith("!")):
            best[key] = ("!" if prefer else "") + cand + "\x00" + url
    for v in best.values():
        title, url = v.lstrip("!").split("\x00")
        res.items.append({"title": title, "url": url, "row": title})
    res.found = len(res.items)
    return res


def _netfunnel(html: str) -> bool:
    return bool(re.search(r"netFunnelAction|net_funnel\.config", html))


# ───────────────────────── 판매처 ─────────────────────────

def everline() -> Result:
    url = "https://everlineshop.com/events"
    res = Result()
    for a, link in _anchors(fetch(url), url, r"^/events/[0-9a-f-]{20,}"):
        t = text_of(a)
        start, end = parse_period(t)
        title = re.sub(r"^.*?\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\s*~\s*\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\s*", "", t)
        title = re.sub(r"^(진행중|종료|마감|예정)\s*", "", title)
        res.found += 1
        res.items.append({"title": clean_title(title), "url": link, "row": t, "start": start, "end": end,
                          "current_list": True})
    return res


def m2u() -> Result:
    url = "https://m2urecord.com/board/m2u-event/1/"
    return _board(fetch(url), url, r"/article/m2u-event/1/\d+")


def beatroad() -> Result:
    url = "https://beatroad.co.kr/product/list.html?cate_no=150"
    return _products(fetch(url), url, r"^/product/(?!list)")


def hellolive() -> Result:
    url = "https://hellolive.tv/ko/category/event"
    html = render(url, budget_ms=20000)
    # 늦게 뜨는 날이 있다: 카드가 없거나, 카드는 있는데 기간 글자가 아직 안 그려졌으면 한 번 더 넉넉하게
    if "/ko/detail/" not in html or not re.search(r"20\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\s+\d{1,2}:\d{2}\s*~", html):
        html = render(url, budget_ms=35000, timeout=100)
    res = Result()
    for a, link in _anchors(html, url, r"^/ko/detail/\d+"):
        t = text_of(a)
        res.found += 1
        start, end = parse_period(t)
        if not end:
            continue  # 기간 없는 카드는 지난 기획전 배너
        head = re.split(r"\s20\d{2}\.\s*\d{1,2}\.\s*\d{1,2}", t)[0]
        artist = head.split("[")[0].strip() if "[" in head else ""
        title = head[len(artist):].strip() if artist else head
        res.items.append({"title": clean_title(title), "url": link, "row": t, "start": start, "end": end,
                          "artist": artist or None, "current_list": True, "render_detail": True})
    return res


def musicart() -> Result:
    url = "https://musicart.kr/m/board.html?code=musicart_board4"
    return _board(fetch(url), url, r"code=musicart_board4.*type=v.*num1=\d+", strip_tail=r"(\s+N)?\s+뮤직아트$")


def withmuu() -> Result:
    url = "https://withmuu.com/goods/event_sale_list.php?type=ing"
    html = render(url, budget_ms=25000, timeout=90)
    if _netfunnel(html) and "event_sale.php?sno=" not in html:
        raise Blocked("접속 대기열을 통과하지 못했어요")
    res = Result()
    seen = set()
    for a, link in _anchors(html, url, r"event_sale\.php\?sno=\d+"):
        t = clean_title(text_of(a))
        if len(t) < 4 or link in seen:
            continue
        seen.add(link)
        res.found += 1
        # 목록의 기간은 응모 기간이 아니라 게시 기간이라 쓰지 않는다 (행사일은 제목에 있음)
        res.items.append({"title": t, "url": link, "row": row_text(a), "no_detail": True})
    return res


def makestar() -> Result:
    api = "https://new-commerce-api.makestar.com/v2/commerce/product_event/events/?saleStatus=ON_SALE&size=100"
    data = fetch_json(api)
    res = Result()
    for e in data.get("data", []):
        res.found += 1
        title = (e.get("title") or {}).get("ko") or (e.get("title") or {}).get("en") or ""
        art = ((e.get("product") or {}).get("artist") or {}).get("i18nName") or {}
        ko, en = art.get("ko"), art.get("en")
        artist = ko if not en or ko == en else f"{en} ({ko})" if re.match(r"[A-Za-z0-9]", en or "") else ko
        sales = e.get("salesData") or {}
        info = e.get("eventInfo") or {}
        hold = (info.get("eventHoldingData") or [{}])[0] or {}

        def iso(v):
            try:
                return datetime.fromisoformat(v.replace(" ", "T")).replace(tzinfo=None).replace(tzinfo=KST) if v else None
            except ValueError:
                return None
        event_date = iso(hold.get("date")) if hold.get("date") else None
        res.items.append({
            "title": title, "url": f"https://www.makestar.com/product/{e['id']}", "row": title,
            "artist": artist, "start": iso(sales.get("salesStartAt")), "end": iso(sales.get("salesEndAt")),
            "event_date": event_date.date() if event_date else None,
            "winner": iso(info.get("winnerAnnounceAt")),
            "hint": ((e.get("eventCategory") or {}).get("name") or ""), "current_list": True, "no_detail": True,
        })
    return res


def jumpup() -> Result:
    url = "https://jumpupent.com/product/list.html?cate_no=48"
    return _products(fetch(url), url, r"/product/detail\.html\?product_no=\d+")


def soundwave() -> Result:
    url = "https://sound-wave.co.kr/board/event-notice/1/"
    return _board(fetch(url), url, r"/board/free/read\.html\?no=\d+&board_no=1")


def musicplant() -> Result:
    url = "https://m.musicplant.co.kr/board/?db=gallery_1&cate=18"
    html = fetch(url)
    res = Result()
    s = soup(html)
    for a in s.find_all("a", href=True):
        m = re.search(r"mariExec\('view@view',\s*'(\d+)'\)", a["href"])
        if not m:
            continue
        title = clean_title(text_of(a))
        if len(title) < 4:
            continue
        res.found += 1
        # 뮤직플랜트는 응모가 끝나면 같은 글 제목을 「… 당첨자 발표」로 바꾼다 → 응모 마감된 행사로 본다
        winner = bool(re.search(r"당첨\s*자?\s*발표", title))
        res.items.append({"title": title, "row": row_text(a), "winner_posted": winner,
                          "url": f"https://m.musicplant.co.kr/board/?db=gallery_1&no={m.group(1)}&mari_mode=view@view"})
    return res


def kpop2gether() -> Result:
    url = "https://www.kpop2gether.com/board/board.html?code=912enter_board4"
    return _board(fetch(url), url, r"code=912enter_board4.*type=v.*num1=\d+")


def applemusic() -> Result:
    url = "https://applemusic.co.kr/board/board.html?code=applemusic_board2"
    res = _board(fetch(url), url, r"code=applemusic_board2&num1=\d+")
    for it in res.items:   # 목록 링크엔 type=v가 없어 본문 대신 목록이 열린다
        n = re.search(r"num1=(\d+)", it["url"]).group(1)
        it["url"] = f"https://applemusic.co.kr/board/board.html?code=applemusic_board2&page=1&type=v&num1={n}&num2=00000"
    return res


def dearmymuse() -> Result:
    url = "https://dearmymuse.com/category/event/65/"
    return _products(fetch(url), url, r"^/product/(?!list)[^/]+/\d+/")


def ktown4u() -> Result:
    url = "https://kr.ktown4u.com/event"
    res = _board(fetch(url), url, r"/eventinfo\?eve_no=\d+", strip_tail=r"\s*이벤트 보기$")
    for it in res.items:
        it["current_list"] = True
    return res


def bdm() -> Result:
    res = Result()
    for mcode in ("001", "002"):
        url = f"https://thebdm.co.kr/shop/shopbrand.html?xcode=003&type=N&mcode={mcode}"
        html = fetch(url)
        r = _products(html, url, r"shopdetail\.html\?branduid=\d+")
        res.items += r.items
        res.found += r.found
        if "검색결과 정렬" in html or "shopbrand" in html:
            res.empty_ok = True
    return res


def starlike() -> Result:
    url = "https://starlikemusic.com/category/event/69/"
    return _products(fetch(url), url, r"^/product/(?!list)[^/]+/\d+/")


def whosfan() -> Result:
    url = "https://whosfanstore.com/collections/offevent"
    html = render(url, budget_ms=8000)
    r = _products(html, url, r"^/(?:collections/[^/]+/)?products/[^/?#]+")
    for it in r.items:
        it["url"] = it["url"].split("?")[0]
    return r


def aladin() -> Result:
    url = "https://www.aladin.co.kr/events/wevent_main_music.aspx"
    html = fetch(url)
    res = _board(html, url, r"wevent\.aspx\?EventId=\d+")
    res.empty_ok = res.found > 0
    for it in res.items:
        it["current_list"] = True
    return res


# (가게 이름, 사이트에서 사람이 보는 목록 주소, 수집 함수)
SOURCES = [
    ("에버라인", "https://everlineshop.com/events", everline),
    ("엠투유", "https://m2urecord.com/board/m2u-event/1/", m2u),
    ("비트로드", "https://beatroad.co.kr/product/list.html?cate_no=150", beatroad),
    ("헬로라이브", "https://hellolive.tv/ko/category/event", hellolive),
    ("뮤직아트", "https://musicart.kr/m/board.html?code=musicart_board4", musicart),
    ("위드뮤", "https://withmuu.com/goods/event_sale_list.php?type=ing", withmuu),
    ("메이크스타", "https://www.makestar.com/event?status=ON_SALE", makestar),
    ("점프업이엔티", "https://jumpupent.com/product/list.html?cate_no=48", jumpup),
    ("사운드웨이브", "https://sound-wave.co.kr/board/event-notice/1/", soundwave),
    ("뮤직플랜트", "https://m.musicplant.co.kr/board/?db=gallery_1&cate=18", musicplant),
    ("케이팝투게더", "https://www.kpop2gether.com/board/board.html?code=912enter_board4", kpop2gether),
    ("애플뮤직", "https://applemusic.co.kr/board/board.html?code=applemusic_board2", applemusic),
    ("디어마이뮤즈", "https://dearmymuse.com/category/event/65/", dearmymuse),
    ("케타포", "https://kr.ktown4u.com/event", ktown4u),
    ("블루드림미디어", "https://thebdm.co.kr/shop/shopbrand.html?xcode=003&type=N", bdm),
    ("스타라이크뮤직", "https://starlikemusic.com/category/event/69/", starlike),
    ("후즈팬", "https://whosfanstore.com/collections/offevent", whosfan),
    ("알라딘", "https://www.aladin.co.kr/events/wevent_main_music.aspx", aladin),
]
