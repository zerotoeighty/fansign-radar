"""PC 쪽 할 일 (서버 수집으로 옮긴 뒤, config.json의 "cloud_mode": true일 때).

1. 서버(GitHub Actions)에서 막히는 판매처(에버라인)만 수집 → data/pc_everline.json
2. 그 결과와 관심 아티스트·알림 설정(토큰 제외)을 GitHub 저장소의 `pc` 브랜치에 올린다
3. 서버가 만든 최신 events.json을 받아 와 PC 화면(127.0.0.1:8771)에 보여 준다

알림은 서버만 보낸다 (PC는 보내지 않음 → 중복 알림 없음).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import requests

from .collect import CONFIG, DATA, EVENTS, PC_EVERLINE, collect, load_json, log, save_json

REPO = "zerotoeighty/fansign-radar"
PUSH_URL = f"https://zerotoeighty@github.com/{REPO}.git"
RAW_EVENTS = f"https://raw.githubusercontent.com/{REPO}/data/events.json"
BRANCH_DIR = os.path.join(DATA, "_pc_branch")
SETTINGS_KEYS = ("favorites", "notify", "include_overseas_alert")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _git(*args, check=True):
    env = dict(os.environ, GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", *args], cwd=BRANCH_DIR, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", env=env, timeout=120, creationflags=_NO_WINDOW)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def push_pc_files() -> bool:
    """pc_everline.json + settings.json을 `pc` 브랜치에 올린다. 바뀐 게 없으면 올리지 않는다."""
    os.makedirs(BRANCH_DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(BRANCH_DIR, ".git")):
        _git("init", "-q", "-b", "pc")
        _git("remote", "add", "origin", PUSH_URL)
        _git("config", "user.name", "zerotoeighty")
        _git("config", "user.email", "zerotoeighty@users.noreply.github.com")
    if _git("fetch", "-q", "origin", "pc", check=False).returncode == 0:
        _git("reset", "-q", "--hard", "origin/pc")
    shutil.copyfile(PC_EVERLINE, os.path.join(BRANCH_DIR, "pc_everline.json"))
    cfg = load_json(CONFIG, {})
    settings = {k: cfg[k] for k in SETTINGS_KEYS if k in cfg}
    with open(os.path.join(BRANCH_DIR, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=1)
    _git("add", "-A")
    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    _git("commit", "-q", "-m", "PC 수집: 에버라인 + 설정")
    _git("push", "-q", "origin", "HEAD:pc")
    return True


def pull_cloud_events() -> bool:
    """서버가 만든 최신 events.json을 받아 PC 화면용으로 저장."""
    r = requests.get(RAW_EVENTS, timeout=20, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    data = r.json()
    old = load_json(EVENTS, {})
    if data.get("checked_at", "") >= old.get("checked_at", ""):
        save_json(EVENTS, data)
        return True
    return False


def run():
    try:
        collect(notify=False, pc_export=True)
    except Exception as e:  # noqa: BLE001
        log(f"PC 수집 오류: {e}")
    try:
        if os.path.exists(PC_EVERLINE):
            log("  GitHub에 올림" if push_pc_files() else "  GitHub: 바뀐 것 없음")
    except Exception as e:  # noqa: BLE001
        log(f"  GitHub 올리기 실패: {e}")
    try:
        if pull_cloud_events():
            log("  서버 결과 받아 옴")
    except Exception as e:  # noqa: BLE001
        log(f"  서버 결과 받기 실패: {e}")


def cloud_mode() -> bool:
    return bool(load_json(CONFIG, {}).get("cloud_mode"))


if __name__ == "__main__":
    run()
    sys.exit(0)
