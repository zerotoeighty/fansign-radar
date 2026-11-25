"""서버 수집 실행이 실패하면 관리자에게 텔레그램 한 통."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from radar.telegram import send_admin  # noqa: E402

url = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    ok = send_admin("🚨 <b>팬싸 레이더 서버 수집 실패</b>\n이번 실행이 오류로 멈췄어요. 다음 정각에 다시 시도해요.\n" + url)
    print("관리자 알림 보냄" if ok else "관리자 알림 설정 없음")
except Exception as e:  # noqa: BLE001
    print("관리자 알림 실패:", e)
