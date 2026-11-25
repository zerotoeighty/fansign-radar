"""아티스트 이름 통일: 판매처마다 다른 표기(채령 / CHAERYEONG / 채령(ITZY) / CHAERYEONG (채령))를
'영문 (한글)' 한 가지로 맞춘다.

수집할 때마다 'EN (한글)'·'한글 (EN)' 짝을 모아 data/artist_names.json에 쌓고(학습),
한쪽 이름만 있는 공지도 짝을 찾아 채운다. 손으로 고칠 땐 artist_fix.json에 적는다.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

from .common import ROOT, artist_from

LEARNED = os.path.join(ROOT, "data", "artist_names.json")
MANUAL = os.path.join(ROOT, "artist_fix.json")   # {"아무 표기": "통일할 이름"} — 사람이 고치는 곳

HANGUL = re.compile(r"[가-힣]")
# 솔로 표기에 괄호로 붙는 '소속 그룹' — 번역 짝이 아님 (예: 채령(ITZY), 나연 (TWICE))
GROUPS = {
    "twice": "트와이스", "itzy": "있지", "nct": "엔시티", "exo": "엑소", "seventeen": "세븐틴", "bts": "방탄소년단",
    "redvelvet": "레드벨벳", "blackpink": "블랙핑크", "aespa": "에스파", "wjsn": "우주소녀", "txt": "투모로우바이투게더",
    "mamamoo": "마마무", "gfriend": "여자친구", "lesserafim": "르세라핌", "apink": "에이핑크", "ohmygirl": "오마이걸",
    "kep1er": "케플러", "fromis9": "프로미스나인", "fromis_9": "프로미스나인", "stayc": "스테이씨", "ive": "아이브",
    "nmixx": "엔믹스", "illit": "아일릿", "babymonster": "베이비몬스터", "straykids": "스트레이키즈", "ateez": "에이티즈",
    "enhypen": "엔하이픈", "theboyz": "더보이즈", "monstax": "몬스타엑스", "shinee": "샤이니", "superjunior": "슈퍼주니어",
    "girlsgeneration": "소녀시대", "snsd": "소녀시대", "winner": "위너", "ikon": "아이콘", "treasure": "트레저",
    "nctdream": "엔시티드림", "nct127": "엔시티127", "wayv": "웨이션브이", "riize": "라이즈", "zerobaseone": "제로베이스원",
    "boynextdoor": "보이넥스트도어", "tws": "투어스", "kissoflife": "키스오브라이프", "viviz": "비비지", "triples": "트리플에스",
}


def key(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def split_pair(name: str):
    """'A (B)' → (영문, 한글). 짝이 아니면 (영문 또는 None, 한글 또는 None)."""
    name = " ".join(name.split())
    m = re.match(r"^(.+?)\s*\(([^()]+)\)$", name)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        ha, hb = bool(HANGUL.search(a)), bool(HANGUL.search(b))
        if ha != hb:
            en, ko = (b, a) if ha else (a, b)
            # 괄호 속이 소속 그룹이면 번역 짝이 아님
            g = key(en)
            if g in GROUPS and key(ko) != key(GROUPS[g]):
                return None, ko if ha else None
            return en, ko
    if HANGUL.search(name):
        return None, name
    return name, None


def _pretty_en(cands: dict[str, int]) -> str:
    """가장 많이 쓰인 이름을 고르고, 같은 이름의 여러 표기(Sung Han Bin / SUNGHANBIN) 중엔 대문자가 많은 쪽."""
    by_key: dict[str, int] = defaultdict(int)
    for name, n in cands.items():
        by_key[key(name)] += n
    top = max(by_key.items(), key=lambda kv: kv[1])[0]
    same = [n for n in cands if key(n) == top]
    return max(same, key=lambda n: (sum(c.isupper() for c in n) / max(1, sum(c.isalpha() for c in n)), -n.count(" "), cands[n]))


class Names:
    def __init__(self):
        raw = {}
        try:
            with open(LEARNED, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            pass
        # ko_key → {영문표기: 표수}, en_key → {한글표기: 표수}
        self.ko2en: dict[str, dict[str, int]] = defaultdict(dict, {k: dict(v) for k, v in raw.get("ko2en", {}).items()})
        self.en2ko: dict[str, dict[str, int]] = defaultdict(dict, {k: dict(v) for k, v in raw.get("en2ko", {}).items()})
        self.ko_pretty: dict[str, str] = raw.get("ko_pretty", {})
        try:
            with open(MANUAL, encoding="utf-8") as f:
                self.manual = {key(k): v for k, v in json.load(f).items() if not k.startswith("_")}
        except (OSError, ValueError):
            self.manual = {}
        # 직접 지정한 이름의 영문·한글 짝은 다른 표기(82MAJOR(82메이저) 등)에도 이긴다
        self._manual_pairs = {}
        for v in self.manual.values():
            en, ko = split_pair(v)
            if en and ko:
                self._manual_pairs[key(en)] = v
                self._manual_pairs[key(ko)] = v

    def learn(self, name: str, weight: int = 1):
        en, ko = split_pair(name)
        if not (en and ko):
            return
        kk, ek = key(ko), key(en)
        if not kk or not ek:
            return
        self.ko2en[kk][en] = self.ko2en[kk].get(en, 0) + weight
        self.en2ko[ek][ko] = self.en2ko[ek].get(ko, 0) + weight
        self.ko_pretty.setdefault(kk, ko)

    def canonical(self, name: str) -> str:
        if not name:
            return name
        if key(name) in self.manual:
            return self.manual[key(name)]
        en, ko = split_pair(name)
        for part in (en, ko):
            if part and key(part) in self._manual_pairs:
                return self._manual_pairs[key(part)]
        if ko and not en:
            cands = self.ko2en.get(key(ko))
            if cands:
                en = _pretty_en(cands)
        elif en and not ko:
            cands = self.en2ko.get(key(en))
            if cands:
                ko = max(cands.items(), key=lambda kv: kv[1])[0]
            elif key(en) in GROUPS:
                ko = GROUPS[key(en)]
        if en and ko:
            cands = self.ko2en.get(key(ko))
            if cands and key(_pretty_en(cands)) == key(en):
                en = _pretty_en(cands)   # 'Sung Han Bin' / 'SUNGHANBIN' 중 많이 쓰인 쪽
            out = f"{en} ({ko})"
        else:
            out = en or ko or name
        return self.manual.get(key(out), out)

    def save(self):
        os.makedirs(os.path.dirname(LEARNED), exist_ok=True)
        with open(LEARNED + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"ko2en": self.ko2en, "en2ko": self.en2ko, "ko_pretty": self.ko_pretty}, f,
                      ensure_ascii=False, indent=1)
        os.replace(LEARNED + ".tmp", LEARNED)


def unify(events: list[dict]):
    """events의 artist를 통일된 이름으로 바꾼다 (원래 표기는 artist_raw에 남김)."""
    names = Names()
    for ev in events:
        # 메이크스타 API 이름은 공식 짝이라 무게를 더 준다
        names.learn(ev["artist"], weight=3 if ev["store"] == "메이크스타" else 1)
        names.learn(artist_from(ev["title"]))
    for ev in events:
        ev["artist_raw"] = ev["artist"]
        ev["artist"] = names.canonical(ev["artist"])
    names.save()
