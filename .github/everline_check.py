"""서버에서 에버라인을 여러 방식으로 열어 보고, 응답 상태·헤더·본문 앞부분을 알림으로 남긴다."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
from radar.common import UA

def note(title, msg):
    msg = msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={title}::{msg[:6000]}")

try:
    ip = requests.get("https://ipinfo.io/json", timeout=10).json()
    note("서버 위치", f"{ip.get('country')} {ip.get('region')} {ip.get('city')} / {ip.get('org')}")
except Exception as e:
    note("서버 위치", f"확인 실패: {e}")

tries = {
    "수집기와 같은 요청": {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6"},
    "브라우저 헤더 전체": {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br"},
    "헤더 없음": {},
}
for url in ("https://everlineshop.com/events", "https://everlineshop.com/", "https://everlineshop.com/robots.txt"):
    for name, h in tries.items():
        try:
            r = requests.get(url, headers=h, timeout=20)
            hd = "\n".join(f"{k}: {v}" for k, v in r.headers.items())
            body = r.text[:1500] if r.status_code != 200 else f"(정상, {len(r.text)}자)"
            note(f"{r.status_code} {url.split('.com')[1] or '/'} [{name}]", f"{hd}\n---\n{body}")
        except Exception as e:
            note(f"오류 {url} [{name}]", repr(e))
        if url != "https://everlineshop.com/events":
            break
