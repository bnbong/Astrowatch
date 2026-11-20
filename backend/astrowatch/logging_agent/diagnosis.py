"""심화 F3020: 이상으로 판정한 실행에만 붙이는 LLM 원인 진단.

- 판정(AuditResult.status, findings)은 audit.py 의 규칙이 만든다. 여기서는 판정을 바꾸지 않는다.
- LLM 호출 조건: status == ANOMALY 이고 AUDIT_LLM 이 켜져 있고 LLM 을 쓸 수 있을 때. 그 밖에는 호출하지 않고 None.
- 어떤 예외도 밖으로 내지 않는다. 실패하면 AuditDiagnosis(failed=True, text="진단 실패: ...").
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from astrowatch.config import get_settings
from astrowatch.schemas.audit import AuditDiagnosis, AuditResult, AuditStatus

log = logging.getLogger(__name__)

MAX_EVENTS = 60

SYSTEM = (
    "너는 천체 관측 계획 Agent 의 실행 로그를 검토하는 로깅 Agent 다. "
    "규칙 엔진이 이미 이상으로 판정한 경고(findings)와 실행 이벤트 목록을 받는다. 판정을 다시 하지 말고 원인만 진단한다. "
    "culprit 은 문제가 있는 쪽 하나: planner(계획 수립, 재계획 전략), worker(Tool 실행, 재시도), tool(Tool 자체 오류, 외부 API), unknown. "
    "fix 는 무엇을 고치면 되는지 한 줄. text 는 한국어 세 문장 이내로 원인과 근거 seq 를 짧게 설명한다."
)


class DiagnosisOut(BaseModel):
    culprit: Literal["planner", "worker", "tool", "unknown"] = "unknown"
    fix: str = Field(default="", description="무엇을 고치면 되는지 한 줄")
    text: str = Field(default="", description="세 문장 이내 한국어 진단")


def _get(e: Any, key: str) -> Any:
    return e.get(key) if isinstance(e, dict) else getattr(e, key, None)


def compact_events(result: AuditResult, events: Iterable[Any]) -> list[str]:
    """판정 구간의 이벤트를 한 줄씩 (args, summary, reason 정도만)."""
    lines: list[str] = []
    for e in sorted(events, key=lambda x: int(_get(x, "seq") or 0)):
        seq = int(_get(e, "seq") or 0)
        etype = str(getattr(_get(e, "type"), "value", _get(e, "type")))
        if not (result.seq_from <= seq <= result.seq_to) or etype.startswith("audit_"):
            continue
        p = _get(e, "payload") or {}
        bits = [f"#{seq}", etype]
        if _get(e, "stage"):
            bits.append(f"stage={_get(e, 'stage')}")
        if _get(e, "tool"):
            bits.append(f"tool={_get(e, 'tool')}")
        for k in ("args", "planned_tools", "summary", "reason", "round", "limit_reached", "ok"):
            if k in p:
                bits.append(f"{k}={json.dumps(p[k], ensure_ascii=False, default=str)}")
        line = " ".join(bits)
        lines.append(line if len(line) <= 200 else line[:199] + "…")
    if len(lines) > MAX_EVENTS:
        lines = lines[:20] + [f"... ({len(lines) - MAX_EVENTS + 20}건 생략) ..."] + lines[-(MAX_EVENTS - 20):]
    return lines


def three_sentences(text: str) -> str:
    parts = [s.strip() for s in re.split(r"(?<=[.!?。])\s+", (text or "").strip()) if s.strip()]
    out = " ".join(parts[:3])
    return out if len(out) <= 600 else out[:599] + "…"


def _invoke(messages: list[Any]) -> DiagnosisOut:
    from astrowatch.agent.llm import get_chat_model, structured_methods

    model = get_chat_model()
    last: Exception | None = None
    for method in structured_methods():
        try:
            res = model.with_structured_output(DiagnosisOut, method=method).invoke(messages)
            if isinstance(res, DiagnosisOut):
                return res
            if isinstance(res, dict):
                return DiagnosisOut.model_validate(res)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError("structured output 실패") from last


def should_diagnose(result: AuditResult | None) -> bool:
    if result is None or result.status != AuditStatus.ANOMALY or not get_settings().audit_llm:
        return False
    from astrowatch.agent.llm import llm_available

    return llm_available()


def diagnose(result: AuditResult, events: Iterable[Any]) -> AuditDiagnosis | None:
    """이상(ANOMALY) 판정에만 LLM 진단. 정상, 판정 불가, LLM 미설정이면 호출 없이 None."""
    try:
        if not should_diagnose(result):
            return None
    except Exception:  # noqa: BLE001
        return None
    model = None
    try:
        from astrowatch.agent.llm import model_name

        model = model_name()
        payload = {
            "findings": [f.model_dump(mode="json") for f in result.findings],
            "events": compact_events(result, events),
        }
        out = _invoke([("system", SYSTEM), ("human", json.dumps(payload, ensure_ascii=False))])
        if not (out.text or "").strip():
            raise ValueError("빈 진단")
        fix = (out.fix or "").strip()
        return AuditDiagnosis(
            source="llm", culprit=out.culprit, fix=fix if len(fix) <= 300 else fix[:299] + "…",
            text=three_sentences(out.text), model=model,
        )
    except Exception as exc:  # noqa: BLE001 (진단 실패가 판정, 실행 로그, 응답을 막지 않는다)
        log.warning("로깅 Agent 진단 실패: %s", type(exc).__name__)
        return AuditDiagnosis(source="llm", failed=True, model=model, text=f"진단 실패: {type(exc).__name__}")
