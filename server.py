"""팬싸 레이더 로컬 서버 — http://127.0.0.1:8771

화면(web/)과 수집 결과(data/events.json)를 보여 주고, 관심 아티스트·텔레그램 설정을 config.json에 저장한다.
이 PC에서만 열린다(127.0.0.1).
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from radar import telegram  # noqa: E402
from radar.collect import CONFIG, EVENTS, collect, load_config, load_json, log, save_json  # noqa: E402

PORT = 8771
WEB = os.path.join(ROOT, "web")
_state = {"running": False, "last_error": None}


def public_config(cfg: dict) -> dict:
    tg = cfg.get("telegram") or {}
    return {
        "favorites": cfg.get("favorites", []),
        "notify": cfg.get("notify", "favorites"),
        "include_overseas_alert": cfg.get("include_overseas_alert", False),
        "telegram": {
            "has_token": bool(tg.get("token")),
            "bot": tg.get("bot_name"),
            "chat": tg.get("chat_name"),
            "connected": bool(tg.get("token") and tg.get("chat_id")),
            "enabled": bool(tg.get("enabled")),
        },
    }


def run_collect_bg(notify=True):
    if _state["running"]:
        return False

    def job():
        _state["running"] = True
        _state["last_error"] = None
        try:
            collect(notify=notify)
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = str(e)
            log(f"수집 오류: {e}")
        finally:
            _state["running"] = False
    threading.Thread(target=job, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 콘솔 조용히
        pass

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    # ── GET ──
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/events":
            data = load_json(EVENTS, {"events": [], "sources": [], "checked_at": None})
            data["config"] = public_config(load_config())
            data["running"] = _state["running"]
            return self._json(data)
        if path == "/api/status":
            return self._json({"running": _state["running"], "error": _state["last_error"]})
        if path == "/":
            path = "/index.html"
        file = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not file.startswith(WEB) or not os.path.isfile(file):
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(file)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(file, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── POST ──
    def do_POST(self):
        if not self._same_origin():
            return self._json({"error": "forbidden"}, 403)
        path = self.path.split("?")[0]
        body = self._body()
        cfg = load_config()
        tg = cfg.setdefault("telegram", {})
        try:
            if path == "/api/collect":
                started = run_collect_bg(notify=True)
                return self._json({"started": started, "running": True})

            if path == "/api/favorites":
                favs = [str(x).strip() for x in body.get("favorites", []) if str(x).strip()]
                cfg["favorites"] = list(dict.fromkeys(favs))[:300]
                save_json(CONFIG, cfg)
                return self._json(public_config(cfg))

            if path == "/api/settings":
                if body.get("notify") in ("favorites", "all"):
                    cfg["notify"] = body["notify"]
                if "include_overseas_alert" in body:
                    cfg["include_overseas_alert"] = bool(body["include_overseas_alert"])
                if "enabled" in body:
                    tg["enabled"] = bool(body["enabled"]) and bool(tg.get("chat_id"))
                save_json(CONFIG, cfg)
                return self._json(public_config(cfg))

            if path == "/api/telegram/token":
                token = str(body.get("token", "")).strip()
                me = telegram.get_me(token)
                tg.update(token=token, bot_name=me.get("username"), chat_id="", chat_name="", enabled=False)
                save_json(CONFIG, cfg)
                return self._json(public_config(cfg))

            if path == "/api/telegram/find":
                if not tg.get("token"):
                    return self._json({"error": "먼저 봇 토큰을 넣어 주세요."}, 400)
                chat = telegram.find_chat(tg["token"])
                if not chat:
                    return self._json({"error": "봇에게 온 메시지를 못 찾았어요. 텔레그램에서 봇에게 /start 를 보낸 뒤 다시 눌러 주세요."}, 404)
                tg.update(chat_id=chat["id"], chat_name=chat["name"], enabled=True)
                save_json(CONFIG, cfg)
                telegram.send(tg["token"], chat["id"],
                              "✅ 팬싸 레이더와 연결됐어요.\n새 팬사인회 공지가 올라오면 여기로 알려 드릴게요.")
                return self._json(public_config(cfg))

            if path == "/api/telegram/test":
                if not (tg.get("token") and tg.get("chat_id")):
                    return self._json({"error": "텔레그램이 아직 연결되지 않았어요."}, 400)
                data = load_json(EVENTS, {"events": []})
                sample = next(iter(data.get("events") or []), None)
                if sample:
                    telegram.send(tg["token"], tg["chat_id"], "🔔 테스트 알림이에요. 실제 알림은 이렇게 와요.\n\n"
                                  + telegram.format_event(sample))
                else:
                    telegram.send(tg["token"], tg["chat_id"], "🔔 테스트 알림이에요.")
                return self._json({"ok": True})

            if path == "/api/telegram/reset":
                cfg["telegram"] = {"token": "", "chat_id": "", "enabled": False}
                save_json(CONFIG, cfg)
                return self._json(public_config(cfg))
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)
        return self._json({"error": "not found"}, 404)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    data = load_json(EVENTS, {})
    if not data.get("checked_at") or time.time() - os.path.getmtime(EVENTS) > 3600:
        run_collect_bg(notify=True)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"팬싸 레이더: {url}  (이 창을 닫으면 화면이 꺼져요. 수집·알림은 작업 스케줄러가 따로 해요)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
