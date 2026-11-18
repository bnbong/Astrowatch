"""캘린더 공급자 (심화 F3023) — 로컬 SQLite 구현체와 Google Calendar 구현체.

Tool(tools/calendar.py)은 이 Protocol 만 보고, 어떤 구현체인지는 get_provider() 가 설정으로 고른다.
시각은 안팎 모두 현지(settings.timezone, 기본 Asia/Seoul) naive datetime 이다.
오류는 clients.http 의 AuthError / UpstreamError / UpstreamTimeout 으로 바꿔 던진다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from astrowatch.clients.http import AuthError, UpstreamError, UpstreamTimeout
from astrowatch.config import get_settings
from astrowatch.tools.contracts import CalendarEvent

logger = logging.getLogger("astrowatch.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]  # scripts/google_auth.py 와 같게
GOOGLE_TIMEOUT = 15
MAX_PAGES = 10


@runtime_checkable
class CalendarProvider(Protocol):
    name: str

    def list_events(self, date_from: date, date_to: date) -> list[CalendarEvent]:
        """date_from 00:00 ~ date_to 다음 날 00:00 과 겹치는 일정 (시작 시각 순)."""
        ...

    def add_event(
        self, title: str, start_dt: datetime, end_dt: datetime, description: str | None, plan_id: int | None
    ) -> CalendarEvent:
        """일정 1건 등록. html_link 는 공급자가 주면 채운다."""
        ...


def _range(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return datetime.combine(date_from, time.min), datetime.combine(date_to + timedelta(days=1), time.min)


# ── 로컬 구현체 ───────────────────────────────────────────────

class LocalCalendarProvider:
    """SQLite local_calendar 테이블. 키·네트워크 없이 동작을 먼저 확인하는 구현체."""

    name = "local"

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path

    def _path(self) -> Path:
        from astrowatch.storage.plans import ensure_db  # 스키마 보장 (지연 import)

        return ensure_db(self.db_path)

    @staticmethod
    def _to_event(row: Any) -> CalendarEvent:
        return CalendarEvent(
            event_id=f"local-{row['id']}",
            title=row["title"],
            start_dt=datetime.fromisoformat(row["start_dt"]),
            end_dt=datetime.fromisoformat(row["end_dt"]),
            source="local",
            plan_id=row["plan_id"],
        )

    def list_events(self, date_from: date, date_to: date) -> list[CalendarEvent]:
        from astrowatch.storage.db import connect

        lo, hi = _range(date_from, date_to)
        with connect(self._path()) as conn:
            rows = conn.execute(
                "SELECT * FROM local_calendar WHERE start_dt < ? AND end_dt > ? ORDER BY start_dt, id",
                (hi.isoformat(timespec="seconds"), lo.isoformat(timespec="seconds")),
            ).fetchall()
        return [self._to_event(r) for r in rows]

    def add_event(
        self, title: str, start_dt: datetime, end_dt: datetime, description: str | None, plan_id: int | None
    ) -> CalendarEvent:
        from astrowatch.storage.db import connect

        with connect(self._path()) as conn:
            cur = conn.execute(
                "INSERT INTO local_calendar (title, start_dt, end_dt, plan_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    title,
                    start_dt.isoformat(timespec="seconds"),
                    end_dt.isoformat(timespec="seconds"),
                    plan_id,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            row = conn.execute("SELECT * FROM local_calendar WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._to_event(row)


# ── Google 구현체 ─────────────────────────────────────────────

class GoogleCalendarProvider:
    """Google Calendar API v3. 토큰은 scripts/google_auth.py 로 1회 발급한다."""

    name = "google"

    def __init__(
        self,
        token_file: Path | None = None,
        calendar_id: str | None = None,
        timezone: str | None = None,
    ) -> None:
        s = get_settings()
        self.token_file = token_file or s.google_token_file
        self.calendar_id = calendar_id or s.google_calendar_id
        self.tz_name = timezone or s.timezone
        self.tz = ZoneInfo(self.tz_name)
        self._service: Any = None

    # 인증 · 서비스
    def _credentials(self) -> Any:
        if not self.token_file or not Path(self.token_file).exists():
            raise AuthError("Google 토큰 파일 없음")
        from google.auth.exceptions import GoogleAuthError, RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        try:
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        except (ValueError, OSError) as e:
            raise AuthError("Google 토큰 파일 형식 오류") from e
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                raise AuthError("Google 토큰 갱신 거부") from e
            except TransportError as e:
                raise UpstreamError("google_calendar", None, "토큰 갱신 중 네트워크 오류") from e
            except GoogleAuthError as e:
                raise AuthError("Google 인증 실패") from e
            try:  # 갱신한 토큰 저장 (실패해도 이번 호출은 진행)
                Path(self.token_file).write_text(creds.to_json(), encoding="utf-8")
            except OSError:
                logger.warning("갱신한 Google 토큰을 저장하지 못함")
            return creds
        raise AuthError("Google 토큰이 유효하지 않음 (재발급 필요)")

    def _svc(self) -> Any:
        if self._service is None:
            import google_auth_httplib2
            import httplib2
            from googleapiclient import discovery  # 테스트에서 discovery.build 를 교체

            creds = self._credentials()
            authed = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=GOOGLE_TIMEOUT))
            self._service = discovery.build("calendar", "v3", http=authed, cache_discovery=False)
        return self._service

    def _execute(self, request: Any) -> dict[str, Any]:
        from googleapiclient.errors import HttpError

        try:
            return request.execute(num_retries=1)
        except HttpError as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status in (401, 403):
                raise AuthError(f"Google Calendar 인증 실패 (HTTP {status})") from e
            raise UpstreamError("google_calendar", status) from e
        except TimeoutError as e:  # socket.timeout 포함
            raise UpstreamTimeout("google_calendar 시간 초과") from e
        except OSError as e:  # httplib2 연결 오류 등
            raise UpstreamError("google_calendar", None, type(e).__name__) from e

    # 시각 변환 — 보낼 때는 현지 시각 + timeZone, 받을 때는 현지 naive 로
    def _to_google_time(self, value: datetime) -> dict[str, str]:
        naive = value.replace(tzinfo=None)
        return {"dateTime": naive.isoformat(timespec="seconds"), "timeZone": self.tz_name}

    def _from_google_time(self, value: dict[str, Any]) -> datetime:
        if "dateTime" in value:
            parsed = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(self.tz).replace(tzinfo=None)
            return parsed
        return datetime.combine(date.fromisoformat(value["date"]), time.min)  # 종일 일정

    def _to_event(self, item: dict[str, Any]) -> CalendarEvent:
        private = (item.get("extendedProperties") or {}).get("private") or {}
        raw_plan = str(private.get("astrowatch_plan_id") or "")
        return CalendarEvent(
            event_id=str(item["id"]),
            title=item.get("summary") or "(제목 없음)",
            start_dt=self._from_google_time(item["start"]),
            end_dt=self._from_google_time(item["end"]),
            source="google",
            plan_id=int(raw_plan) if raw_plan.isdigit() else None,
            html_link=item.get("htmlLink"),
        )

    def list_events(self, date_from: date, date_to: date) -> list[CalendarEvent]:
        lo, hi = _range(date_from, date_to)
        svc = self._svc()
        events: list[CalendarEvent] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            body = self._execute(
                svc.events().list(
                    calendarId=self.calendar_id,
                    timeMin=lo.replace(tzinfo=self.tz).isoformat(),
                    timeMax=hi.replace(tzinfo=self.tz).isoformat(),
                    timeZone=self.tz_name,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                    pageToken=page_token,
                )
            )
            for item in body.get("items", []):
                if item.get("status") == "cancelled" or "start" not in item or "end" not in item:
                    continue
                events.append(self._to_event(item))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return events

    def add_event(
        self, title: str, start_dt: datetime, end_dt: datetime, description: str | None, plan_id: int | None
    ) -> CalendarEvent:
        body: dict[str, Any] = {
            "summary": title,
            "description": description or "",
            "start": self._to_google_time(start_dt),
            "end": self._to_google_time(end_dt),
        }
        if plan_id is not None:
            body["extendedProperties"] = {"private": {"astrowatch_plan_id": str(plan_id)}}
        item = self._execute(self._svc().events().insert(calendarId=self.calendar_id, body=body))
        return self._to_event(item)


def get_provider() -> CalendarProvider:
    """settings.calendar_provider 로 구현체를 고른다. 모르는 값은 local."""
    if get_settings().calendar_provider == "google":
        return GoogleCalendarProvider()
    return LocalCalendarProvider()
