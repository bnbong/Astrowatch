"""로깅 서브 Agent (F3013). EventBus 구독자 — 받은 이벤트를 run_logs 에 덧붙인다.

LLM 을 쓰지 않는다. 저장 실패는 모두 삼킨다 (메인 흐름 보호).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from astrowatch.events.bus import Event, EventBus
from astrowatch.storage.db import init_db
from astrowatch.storage.run_logs import append_event, max_seq

log = logging.getLogger(__name__)


class LoggingAgent:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path
        self._unsubscribe: Callable[[], None] | None = None
        self._initialized = False

    @property
    def attached(self) -> bool:
        return self._unsubscribe is not None

    def handle(self, event: Event) -> None:
        try:
            append_event(event, db_path=self.db_path)
        except Exception as exc:  # noqa: BLE001 — 로그 저장 실패가 메인 흐름을 막지 않는다
            log.warning("실행 로그 저장 실패: %s", type(exc).__name__)

    def attach(self, bus: EventBus) -> None:
        if self.attached:
            return
        if not self._initialized:
            try:
                init_db(self.db_path)
                self._initialized = True
            except Exception as exc:  # noqa: BLE001
                log.warning("로그 DB 초기화 실패: %s", type(exc).__name__)
        self._unsubscribe = bus.subscribe(self.handle)
        # 재시작 뒤에도 세션 seq 가 이어지도록 저장소의 마지막 seq 를 버스에 알려 준다
        self._bus = bus
        bus.seq_seed = lambda session_id: max_seq(session_id, db_path=self.db_path)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        bus = getattr(self, "_bus", None)
        if bus is not None and bus.seq_seed is not None:
            bus.seq_seed = None
            self._bus = None


_agent: LoggingAgent | None = None


def start_logging_agent(bus: EventBus | None = None) -> LoggingAgent:
    """앱 시작 시 호출. 모듈 싱글턴 — 여러 번 불러도 한 번만 구독한다."""
    global _agent
    if bus is None:
        from astrowatch.events.bus import bus as default_bus

        bus = default_bus
    if _agent is None:
        _agent = LoggingAgent()
    _agent.attach(bus)
    return _agent


def stop_logging_agent() -> None:
    global _agent
    if _agent is not None:
        _agent.detach()
        _agent = None
