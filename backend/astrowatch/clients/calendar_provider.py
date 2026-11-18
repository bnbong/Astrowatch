"""캘린더 공급자 (심화 F3023) — 로컬 SQLite 구현체와 Google Calendar 구현체.

Tool(tools/calendar.py)은 이 Protocol 만 보고, 어떤 구현체인지는 get_provider() 가 설정으로 고른다.
시각은 안팎 모두 현지(settings.timezone, 기본 Asia/Seoul) naive datetime 이다.
오류는 clients.http 의 AuthError / UpstreamError / UpstreamTimeout 으로 바꿔 던진다.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from astrowatch.clients.http import AuthError, UpstreamError, UpstreamTimeout
from astrowatch.config import get_settings
from astrowatch.secure_files import check_private, write_private
from astrowatch.tools.contracts import CalendarEvent

logger = logging.getLogger("astrowatch.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]  # scripts/google_auth.py 와 같게
GOOGLE_TIMEOUT = 15
MAX_PAGES = 10
_TOKEN_LOCK = threading.Lock()  # 토큰 갱신 · 저장 직렬화 (보안 강화 16)


class EventAlreadyExists(Exception):
    """같은 계획의 일정이 이미 있다 (멱등 등록). event 는 기존 일정."""

    def __init__(self, event: CalendarEvent) -> None:
        super().__init__("이미 등록된 일정")
        self.event = event


def deterministic_event_id(plan_id: int, created_at: datetime | str) -> str:
    """Google 이벤트 ID: base32hex 소문자 32자. Google 규칙은 [a-v0-9] 5~1024자.

    같은 계획이면 항상 같은 ID 라서 재시도가 중복 일정을 만들지 않고 409 로 돌아온다.
    """
    stamp = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
    digest = hashlib.sha256(f"astrowatch:{plan_id}:{stamp}".encode("utf-8")).digest()
    return base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")[:32]


@runtime_checkable
class CalendarProvider(Protocol):
    name: str

    def list_events(self, date_from: date, date_to: date) -> list[CalendarEvent]:
        """date_from 00:00 ~ date_to 다음 날 00:00 과 겹치는 일정 (시작 시각 순)."""
        ...

    def add_event(
        self, title: str, start_dt: datetime, end_dt: datetime, description: str | None, plan_id: int | None,
        event_key: str | None = None,
    ) -> CalendarEvent:
        """일정 1건 등록. html_link 는 공급자가 주면 채운다.

        같은 plan_id(또는 event_key) 일정이 이미 있으면 EventAlreadyExists 를 던진다 (멱등).
        """
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
        self, title: str, start_dt: datetime, end_dt: datetime, description: str | None, plan_id: int | None,
        event_key: str | None = None,
    ) -> CalendarEvent:
        from astrowatch.storage.db import connect

        with connect(self._path()) as conn:
            # plan_id 유니크: 테이블은 그대로 두고 같은 트랜잭션(쓰기 잠금)에서 조회 후 삽입
            conn.execute("BEGIN IMMEDIATE")
            if plan_id is not None:
                existing = conn.execute(
                    "SELECT * FROM local_calendar WHERE plan_id = ? ORDER BY id LIMIT 1", (plan_id,)
                ).fetchone()
                if existing is not None:
                    raise EventAlreadyExists(self._to_event(existing))
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
    def _load_creds(self) -> Any:
        """토큰 파일을 읽는다. 읽기 전에 심볼릭 링크 거부 · 권한 점검."""
        from google.oauth2.credentials import Credentials

        if not self.token_file or not Path(self.token_file).exists():
            raise AuthError("Google 토큰 파일 없음")
        check_private(self.token_file)
        try:
            return Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        except (ValueError, OSError):
            raise AuthError("Google 토큰 파일 형식 오류") from None

    @staticmethod
    def _newer(a: Any, b: Any) -> bool:
        """a 가 b 보다 늦게 만료되는 유효 토큰인가."""
        if not getattr(a, "valid", False):
            return False
        if b is None or getattr(b, "expiry", None) is None:
            return True
        return a.expiry is not None and a.expiry > b.expiry

    def _credentials(self) -> Any:
        creds = self._load_creds()
        if creds.valid:
            return creds
        if not (creds.expired and creds.refresh_token):
            raise AuthError("Google 토큰이 유효하지 않음 (재발급 필요)")
        from google.auth.exceptions import GoogleAuthError, RefreshError, TransportError
        from google.auth.transport.requests import Request

        # 갱신 · 저장은 프로세스 전역 잠금 안에서 — 동시 요청이 서로의 토큰을 덮어쓰지 않게
        with _TOKEN_LOCK:
            latest = self._load_creds()  # 기다리는 동안 다른 스레드가 갱신했을 수 있다
            if latest.valid:
                return latest
            if latest.refresh_token:
                creds = latest
            try:
                creds.refresh(Request())
            except RefreshError:
                raise AuthError("Google 토큰 갱신 거부 (재발급 필요)") from None
            except TransportError:
                raise UpstreamError("google_calendar", None, "토큰 갱신 중 네트워크 오류") from None
            except GoogleAuthError:
                raise AuthError("Google 인증 실패") from None
            # 저장 직전 다시 읽어 더 최신 토큰이 있으면 그것을 쓴다 (덮어쓰지 않음)
            try:
                on_disk = self._load_creds()
            except AuthError:
                on_disk = None
            if on_disk is not None and self._newer(on_disk, creds):
                return on_disk
            try:  # 저장 실패는 경고만 (이번 호출은 진행)
                write_private(self.token_file, creds.to_json())
            except OSError as e:
                logger.warning("갱신한 Google 토큰을 저장하지 못함: %s", type(e).__name__)
            return creds

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
        self, title: str, start_dt: datetime, end_dt: datetime, description: str | None, plan_id: int | None,
        event_key: str | None = None,
    ) -> CalendarEvent:
        body: dict[str, Any] = {
            "summary": title,
            "description": description or "",
            "start": self._to_google_time(start_dt),
            "end": self._to_google_time(end_dt),
        }
        if event_key:
            body["id"] = event_key  # 결정적 ID: 재시도 시 409 로 중복 방지
        if plan_id is not None:
            body["extendedProperties"] = {"private": {"astrowatch_plan_id": str(plan_id)}}
        try:
            item = self._execute(self._svc().events().insert(calendarId=self.calendar_id, body=body))
        except UpstreamError as e:
            if e.status != 409 or not event_key:
                raise
            raise EventAlreadyExists(self._existing(event_key, title, start_dt, end_dt, plan_id)) from None
        return self._to_event(item)

    def _existing(
        self, event_key: str, title: str, start_dt: datetime, end_dt: datetime, plan_id: int | None
    ) -> CalendarEvent:
        """409 뒤 기존 일정 조회. 조회가 안 되면 아는 값으로 채운다."""
        try:
            item = self._execute(self._svc().events().get(calendarId=self.calendar_id, eventId=event_key))
            return self._to_event(item)
        except Exception as e:  # noqa: BLE001 — 등록 자체는 이미 된 상태
            logger.info("기존 Google 일정 조회 실패: %s", type(e).__name__)
            return CalendarEvent(
                event_id=event_key, title=title, start_dt=start_dt, end_dt=end_dt, source="google", plan_id=plan_id
            )


def get_provider() -> CalendarProvider:
    """settings.calendar_provider 로 구현체를 고른다. 모르는 값은 local."""
    if get_settings().calendar_provider == "google":
        return GoogleCalendarProvider()
    return LocalCalendarProvider()
