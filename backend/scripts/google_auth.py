"""Google Calendar OAuth 토큰 1회 발급 (심화 F3023).

    cd backend
    python scripts/google_auth.py

브라우저가 열리면 테스트 사용자로 등록한 계정으로 로그인한다.
성공하면 .env 의 GOOGLE_TOKEN_FILE 경로(기본 secrets/google_token.json)에 토큰이 저장된다.
맥북으로 옮길 때는 secrets/ 폴더를 통째로 복사한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrowatch.config import get_settings  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def main() -> int:
    s = get_settings()
    if not s.google_client_secret_file or not s.google_client_secret_file.exists():
        print("클라이언트 JSON 이 없습니다:", s.google_client_secret_file)
        return 1
    token_path = s.google_token_file or (s.root_dir / "secrets/google_token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(s.google_client_secret_file), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print("토큰 저장:", token_path)

    # 연결 확인은 토큰 권한(calendar.events) 안에서 되는 '일정 1건 조회'로 한다.
    # calendars().get 은 더 넓은 권한이 필요해 403 이 난다.
    try:
        from googleapiclient.discovery import build

        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        resp = svc.events().list(calendarId=s.google_calendar_id, maxResults=1, singleEvents=True).execute()
        print("캘린더 연결 확인: OK (캘린더 ID:", s.google_calendar_id, "/ 일정 조회 가능, 시간대:", resp.get("timeZone"), ")")
    except Exception as e:  # noqa: BLE001 — 토큰은 이미 저장됐으므로 확인 실패는 안내만
        print("토큰은 저장됐지만 연결 확인에 실패:", type(e).__name__, "- CALENDAR_PROVIDER=google 로 실행해 보세요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
