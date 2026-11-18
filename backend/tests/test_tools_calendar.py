"""심화 F3023 캘린더 Tool — local 구현체로 동작 확인, Google 은 가짜 서비스로만 (실제 호출 없음)."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from astrowatch.clients import calendar_provider as cp
from astrowatch.config import reset_settings_cache
from astrowatch.schemas.plan import PlanCreate
from astrowatch.storage import plans as plan_store
from astrowatch.tools import calendar as cal
from astrowatch.tools.base import ErrorCode
from astrowatch.tools.contracts import CalendarAddOutput, CalendarQueryOutput, EventKind


def _plan(**kw) -> int:
    data = dict(
        event_title="쌍둥이자리 유성우 극대",
        event_kind=EventKind.METEOR,
        start_dt=datetime(2026, 12, 14, 22, 10),
        end_dt=datetime(2026, 12, 15, 1, 30),
        location_label="서울",
        basis="적합 · 맑음 3시간 · 달 영향 없음",
    )
    data.update(kw)
    return plan_store.create_plan(PlanCreate(**data)).plan.id


# ── 입력 계약 ─────────────────────────────────────────────────

def test_add_input_accepts_plan_id_only():
    res = cal.add_plan_to_calendar.invoke({"plan_id": 1, "title": "지어낸 제목"})
    assert not res.ok and res.code == ErrorCode.BAD_INPUT
    schema = cal.add_plan_to_calendar.to_openai_tool()["function"]["parameters"]
    assert list(schema["properties"]) == ["plan_id"]


def test_registered_in_builtin_registry():
    from astrowatch.tools.registry import load_builtin_tools, registry

    load_builtin_tools()
    if registry.get("get_calendar") is None:  # 다른 테스트가 registry 를 비운 경우
        registry.register(cal.get_calendar)
        registry.register(cal.add_plan_to_calendar)
    assert registry.get("add_plan_to_calendar").kind.value == "local"


# ── local 구현체 ──────────────────────────────────────────────

def test_local_add_then_query():
    pid = _plan()
    res = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert res.ok, res.error
    out: CalendarAddOutput = res.data
    assert out.provider == "local" and not out.already_registered
    assert out.title == "[Astrowatch] 쌍둥이자리 유성우 극대 관측 — 서울"
    assert out.start_dt == datetime(2026, 12, 14, 22, 10) and out.end_dt == datetime(2026, 12, 15, 1, 30)
    assert out.event_id.startswith("local-")
    assert plan_store.get_plan(pid).calendar_event_id == out.event_id

    q = cal.get_calendar.invoke({"date_from": "2026-12-15", "date_to": "2026-12-15"})  # 자정 넘김 구간도 걸린다
    assert q.ok
    assert [e.event_id for e in q.data.events] == [out.event_id]
    assert q.data.events[0].plan_id == pid and q.data.events[0].source == "local"

    empty = cal.get_calendar.invoke({"date_from": "2026-12-16", "date_to": "2026-12-20"})
    assert empty.ok and empty.data.events == []


def test_duplicate_registration_is_blocked():
    pid = _plan()
    first = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    second = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert second.ok and second.data.already_registered
    assert second.data.event_id == first.data.event_id
    assert len(cp.LocalCalendarProvider().list_events(date(2026, 12, 1), date(2026, 12, 31))) == 1


def test_missing_plan_is_not_found():
    res = cal.add_plan_to_calendar.invoke({"plan_id": 999})
    assert not res.ok and res.code == ErrorCode.NOT_FOUND


def test_query_bad_range():
    res = cal.get_calendar.invoke({"date_from": "2026-12-10", "date_to": "2026-12-01"})
    assert not res.ok and res.code == ErrorCode.BAD_INPUT


def test_conflicts_for_overlap_and_exclude():
    prov = cp.LocalCalendarProvider()
    prov.add_event("회식", datetime(2026, 12, 14, 19, 0), datetime(2026, 12, 14, 23, 0), None, None)
    prov.add_event("아침 회의", datetime(2026, 12, 15, 9, 0), datetime(2026, 12, 15, 10, 0), None, None)
    prov.add_event("이 계획", datetime(2026, 12, 14, 22, 10), datetime(2026, 12, 15, 1, 30), None, 7)

    found = cal.conflicts_for(datetime(2026, 12, 14, 22, 10), datetime(2026, 12, 15, 1, 30))
    assert {e.title for e in found} == {"회식", "이 계획"}
    found = cal.conflicts_for(datetime(2026, 12, 14, 22, 10), datetime(2026, 12, 15, 1, 30), exclude_plan_id=7)
    assert [e.title for e in found] == ["회식"]
    # 경계가 맞닿기만 하면 겹침 아님
    assert cal.conflicts_for(datetime(2026, 12, 14, 23, 0), datetime(2026, 12, 15, 1, 0), exclude_plan_id=7) == []


def test_conflicts_for_failure_returns_empty():
    class Broken:
        name = "broken"

        def list_events(self, a, b):
            raise RuntimeError("boom")

    assert cal.conflicts_for(datetime(2026, 12, 14, 22), datetime(2026, 12, 15, 1), provider=Broken()) == []


# ── Google 구현체 (가짜 서비스) ───────────────────────────────

class _Req:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc

    def execute(self, num_retries=0):
        if self.exc:
            raise self.exc
        return self.result


class FakeEvents:
    def __init__(self, svc):
        self.svc = svc

    def list(self, **kw):
        self.svc.calls.append(("list", kw))
        return _Req(self.svc.list_result, self.svc.exc)

    def insert(self, **kw):
        self.svc.calls.append(("insert", kw))
        body = kw["body"]
        return _Req(
            {
                "id": "gev123",
                "summary": body["summary"],
                "start": {"dateTime": body["start"]["dateTime"] + "+09:00"},
                "end": {"dateTime": body["end"]["dateTime"] + "+09:00"},
                "htmlLink": "https://www.google.com/calendar/event?eid=gev123",
                "extendedProperties": body.get("extendedProperties"),
            },
            self.svc.exc,
        )


class FakeService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.list_result: dict = {"items": []}
        self.exc: Exception | None = None
        self.build_kwargs: dict = {}

    def events(self):
        return FakeEvents(self)


@pytest.fixture
def google(tmp_path, monkeypatch):
    """CALENDAR_PROVIDER=google + 가짜 토큰 + discovery.build 교체. 네트워크 없음."""
    token = tmp_path / "google_token.json"
    token.write_text(
        json.dumps({"token": "fake-access", "refresh_token": "fake-refresh",
                    "client_id": "cid", "client_secret": "csecret", "expiry": "2099-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CALENDAR_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "primary")
    monkeypatch.setenv("TIMEZONE", "Asia/Seoul")
    reset_settings_cache()

    svc = FakeService()

    def fake_build(name, version, **kw):
        assert (name, version) == ("calendar", "v3")
        svc.build_kwargs = kw
        return svc

    import googleapiclient.discovery

    monkeypatch.setattr(googleapiclient.discovery, "build", fake_build)
    return svc


def test_google_insert_args(google):
    pid = _plan()
    res = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert res.ok, res.error
    assert res.data.provider == "google" and res.data.event_id == "gev123"
    assert res.data.html_link.startswith("https://www.google.com/calendar/")
    assert res.data.start_dt == datetime(2026, 12, 14, 22, 10)  # 현지 naive 로 되돌림

    kind, kw = google.calls[-1]
    assert kind == "insert" and kw["calendarId"] == "primary"
    body = kw["body"]
    assert body["summary"] == "[Astrowatch] 쌍둥이자리 유성우 극대 관측 — 서울"
    assert body["start"] == {"dateTime": "2026-12-14T22:10:00", "timeZone": "Asia/Seoul"}
    assert body["end"] == {"dateTime": "2026-12-15T01:30:00", "timeZone": "Asia/Seoul"}
    assert "적합" in body["description"]
    assert body["extendedProperties"]["private"]["astrowatch_plan_id"] == str(pid)
    assert plan_store.get_plan(pid).calendar_event_id == "gev123"

    again = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert again.data.already_registered and [c[0] for c in google.calls].count("insert") == 1


def test_google_list_args_and_parsing(google):
    google.list_result = {
        "items": [
            {"id": "a", "summary": "회식", "start": {"dateTime": "2026-12-14T10:00:00Z"},
             "end": {"dateTime": "2026-12-14T14:00:00Z"}, "htmlLink": "https://x"},
            {"id": "b", "summary": "휴가", "start": {"date": "2026-12-15"}, "end": {"date": "2026-12-16"}},
            {"id": "c", "status": "cancelled", "start": {"date": "2026-12-15"}, "end": {"date": "2026-12-16"}},
        ]
    }
    res = cal.get_calendar.invoke({"date_from": "2026-12-14", "date_to": "2026-12-15"})
    assert res.ok, res.error
    kind, kw = google.calls[-1]
    assert kind == "list"
    assert kw["timeMin"] == "2026-12-14T00:00:00+09:00" and kw["timeMax"] == "2026-12-16T00:00:00+09:00"
    assert kw["singleEvents"] is True and kw["orderBy"] == "startTime" and kw["timeZone"] == "Asia/Seoul"
    ev = res.data.events
    assert [e.event_id for e in ev] == ["a", "b"]
    assert ev[0].start_dt == datetime(2026, 12, 14, 19, 0)  # UTC 10시 → 서울 19시
    assert ev[1].start_dt == datetime(2026, 12, 15, 0, 0) and ev[1].source == "google"

    found = cal.conflicts_for(datetime(2026, 12, 14, 22, 0), datetime(2026, 12, 15, 1, 0))
    assert [e.title for e in found] == ["회식", "휴가"]


def test_google_without_token_is_auth(tmp_path, monkeypatch):
    called = []
    import googleapiclient.discovery

    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: called.append(1))
    monkeypatch.setenv("CALENDAR_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "missing.json"))
    reset_settings_cache()
    pid = _plan()

    res = cal.add_plan_to_calendar.invoke({"plan_id": pid})
    assert not res.ok and res.code == ErrorCode.AUTH
    assert plan_store.get_plan(pid).calendar_event_id is None
    q = cal.get_calendar.invoke({"date_from": "2026-12-14", "date_to": "2026-12-15"})
    assert not q.ok and q.code == ErrorCode.AUTH
    assert cal.conflicts_for(datetime(2026, 12, 14, 22), datetime(2026, 12, 15, 1)) == []
    assert called == []


def test_google_http_errors_mapped(google):
    from googleapiclient.errors import HttpError
    from httplib2 import Response

    google.exc = HttpError(Response({"status": 401}), b"{}")
    res = cal.get_calendar.invoke({"date_from": "2026-12-14", "date_to": "2026-12-15"})
    assert res.code == ErrorCode.AUTH

    google.exc = HttpError(Response({"status": 500}), b"{}")
    assert cal.get_calendar.invoke({"date_from": "2026-12-14", "date_to": "2026-12-15"}).code == ErrorCode.UPSTREAM

    google.exc = TimeoutError("slow")
    assert cal.get_calendar.invoke({"date_from": "2026-12-14", "date_to": "2026-12-15"}).code == ErrorCode.TIMEOUT


# ── 공급자 교체 ───────────────────────────────────────────────

def test_switching_provider_keeps_tool_io(google, monkeypatch):
    """local → google 로 바꿔도 Tool 입출력 모델·필드가 같다."""
    pid_local = _plan()
    monkeypatch.setenv("CALENDAR_PROVIDER", "local")
    reset_settings_cache()
    assert isinstance(cp.get_provider(), cp.LocalCalendarProvider)
    local_add = cal.add_plan_to_calendar.invoke({"plan_id": pid_local})
    local_q = cal.get_calendar.invoke({"date_from": "2026-12-14", "date_to": "2026-12-15"})

    pid_google = _plan(start_dt=datetime(2026, 12, 20, 21, 0), end_dt=datetime(2026, 12, 20, 23, 0))
    monkeypatch.setenv("CALENDAR_PROVIDER", "google")
    reset_settings_cache()
    assert isinstance(cp.get_provider(), cp.GoogleCalendarProvider)
    google_add = cal.add_plan_to_calendar.invoke({"plan_id": pid_google})
    google_q = cal.get_calendar.invoke({"date_from": "2026-12-14", "date_to": "2026-12-15"})

    for a, b, model in ((local_add, google_add, CalendarAddOutput), (local_q, google_q, CalendarQueryOutput)):
        assert a.ok and b.ok
        assert type(a.data) is type(b.data) is model
        assert set(a.model_dump(mode="json")["data"]) == set(b.model_dump(mode="json")["data"])
    assert (local_add.data.provider, google_add.data.provider) == ("local", "google")
    assert isinstance(cp.LocalCalendarProvider(), cp.CalendarProvider)
    assert isinstance(cp.GoogleCalendarProvider(), cp.CalendarProvider)
