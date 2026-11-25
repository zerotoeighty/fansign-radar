"""서버용 config.json 만들기: PC가 올린 설정(화면용 관심 아티스트 등)만. 알림은 봇 구독자별로 보낸다(collect.py)."""
import json

try:
    with open("settings.json", encoding="utf-8") as f:
        cfg = json.load(f)
except (OSError, ValueError):
    cfg = {}
cfg["telegram"] = {"token": "", "chat_id": "", "enabled": False}   # 1:1 알림은 쓰지 않음
with open("config.json", "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False)
print("설정 준비 완료")
