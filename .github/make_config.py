"""서버용 config.json 만들기: PC가 올린 설정(관심 아티스트 등) + GitHub Secrets의 텔레그램 토큰."""
import json
import os

try:
    with open("settings.json", encoding="utf-8") as f:
        cfg = json.load(f)
except (OSError, ValueError):
    cfg = {}
token, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
cfg["telegram"] = {"token": token, "chat_id": chat, "enabled": bool(token and chat)}
with open("config.json", "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False)
print("관심 아티스트", len(cfg.get("favorites", [])), "명 / 알림:", cfg.get("notify"), "/ 텔레그램:", "연결" if cfg["telegram"]["enabled"] else "토큰 없음(알림 안 보냄)")
