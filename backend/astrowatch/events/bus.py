"""실행 이벤트 발행·구독 (F3012).

발행하는 쪽(메인 Agent)은 누가 구독하는지 모른다. 구독자가 없어도, 구독자가 예외를 내도
메인 흐름은 멈추지 않는다. 동기 콜백 리스트로 충분하다 (비동기 요구 없음).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    STAGE_START = "stage_start"     # 단계 진입  payload: {}
    STAGE_END = "stage_end"         # 단계 종료  payload: {duration_ms, summary?}
    TOOL_CALL = "tool_call"         # Tool 호출  payload: {args}
    TOOL_RESULT = "tool_result"     # Tool 응답  payload: {ok, error?, note?, data(요약), duration_ms}
    LLM_DECISION = "llm_decision"   # LLM 판단   payload: {decision, reason, model, tokens?}
    REPLAN = "replan"               # 재계획     payload: {round, strategy, reason, limit_reached}
    ERROR = "error"                 # 흐름 오류  payload: {message} (키·스택 없음)
    SESSION_START = "session_start"  # 대화 턴 시작 payload: {message}
    SESSION_END = "session_end"     # 대화 턴 종료 payload: {intent, replan_count, duration_ms}


@dataclass(frozen=True)
class Event:
    session_id: str
    type: EventType
    seq: int
    ts: datetime
    stage: str | None = None
    tool: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "seq": self.seq,
            "ts": self.ts.isoformat(),
            "type": self.type.value,
            "stage": self.stage,
            "tool": self.tool,
            "payload": self.payload,
        }


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._seq: dict[str, int] = {}
        self._lock = threading.Lock()
        # 서버 재시작 뒤 같은 세션이 이어질 때 seq 가 1부터 다시 시작하지 않도록,
        # 저장소가 마지막 seq 를 알려 주는 훅. 버스는 저장소를 모른다 — 구독자(로깅 Agent)가 꽂는다.
        self.seq_seed: Callable[[str], int] | None = None

    # ── 구독 ──
    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        """구독 등록. 반환값을 호출하면 해제."""
        with self._lock:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsubscribe

    def subscriber_count(self) -> int:
        return len(self._subs)

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()
            self._seq.clear()

    # ── 발행 ──
    def publish(
        self,
        session_id: str,
        type: EventType,
        *,
        stage: str | None = None,
        tool: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        with self._lock:
            if session_id not in self._seq and self.seq_seed is not None:
                try:
                    self._seq[session_id] = int(self.seq_seed(session_id) or 0)
                except Exception:  # noqa: BLE001 — 시드 실패는 0부터 시작할 뿐, 발행을 막지 않는다
                    self._seq[session_id] = 0
            seq = self._seq.get(session_id, 0) + 1
            self._seq[session_id] = seq
            subs = list(self._subs)
        event = Event(
            session_id=session_id,
            type=type,
            seq=seq,
            ts=datetime.now(timezone.utc),
            stage=stage,
            tool=tool,
            payload=payload or {},
        )
        for fn in subs:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 — 구독자 장애는 메인 흐름에 영향 없음
                pass
        return event

    def timer(self) -> "Stopwatch":
        return Stopwatch()


class Stopwatch:
    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)


bus = EventBus()
