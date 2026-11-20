"""심화 F3020: 로깅 Agent 판정 저장소 (run_audits).

판정(status, findings_json)과 진단(diagnosis_json)은 다른 열에 둔다. 같은 (session_id, seq_from, seq_to)
를 다시 판정하면 덮어쓴다. 진단이 없거나 실패해도 판정 행은 남는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from astrowatch.schemas.audit import AuditDiagnosis, AuditFinding, AuditResult, AuditStatus
from astrowatch.storage.db import connect
from astrowatch.storage.plans import ensure_db

# 최신 = 가장 늦게 끝난 구간. 같은 끝이면 더 넓은 구간(세션 전체 판정), 그다음 나중에 저장한 행
_LATEST_ORDER = "seq_to DESC, (seq_to - seq_from) DESC, id DESC"


def save_audit(result: AuditResult, *, db_path: Path | None = None) -> None:
    path = ensure_db(db_path)
    findings = json.dumps([f.model_dump(mode="json") for f in result.findings], ensure_ascii=False)
    diagnosis = json.dumps(result.diagnosis.model_dump(mode="json"), ensure_ascii=False) if result.diagnosis else None
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO run_audits (session_id, seq_from, seq_to, status, findings_json, evidence_count,
                                       rule_version, audited_at, diagnosis_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (session_id, seq_from, seq_to) DO UPDATE SET
                 status = excluded.status, findings_json = excluded.findings_json,
                 evidence_count = excluded.evidence_count, rule_version = excluded.rule_version,
                 audited_at = excluded.audited_at, diagnosis_json = excluded.diagnosis_json""",
            (result.session_id, result.seq_from, result.seq_to, result.status.value, findings,
             result.evidence_count, result.rule_version, result.audited_at, diagnosis),
        )


def _loads(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _row(r: Any) -> AuditResult:
    diag = _loads(r["diagnosis_json"], None)
    return AuditResult(
        session_id=r["session_id"], seq_from=r["seq_from"], seq_to=r["seq_to"],
        status=AuditStatus(r["status"]),
        findings=[AuditFinding.model_validate(f) for f in _loads(r["findings_json"], [])],
        evidence_count=r["evidence_count"], rule_version=r["rule_version"], audited_at=r["audited_at"],
        diagnosis=AuditDiagnosis.model_validate(diag) if isinstance(diag, dict) else None,
    )


def get_latest(session_id: str, *, db_path: Path | None = None) -> AuditResult | None:
    path = ensure_db(db_path)
    with connect(path) as conn:
        r = conn.execute(f"SELECT * FROM run_audits WHERE session_id = ? ORDER BY {_LATEST_ORDER} LIMIT 1",
                         (session_id,)).fetchone()
    return _row(r) if r else None


def list_audits(session_id: str, *, db_path: Path | None = None) -> list[AuditResult]:
    """구간 끝 seq 내림차순 (최신 먼저)."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        rows = conn.execute(f"SELECT * FROM run_audits WHERE session_id = ? ORDER BY {_LATEST_ORDER}",
                            (session_id,)).fetchall()
    return [_row(r) for r in rows]


def latest_by_session(*, db_path: Path | None = None) -> dict[str, AuditResult]:
    """세션 목록 배지용: 세션마다 최신 판정 하나."""
    path = ensure_db(db_path)
    with connect(path) as conn:
        rows = conn.execute(f"SELECT * FROM run_audits ORDER BY session_id, {_LATEST_ORDER}").fetchall()
    out: dict[str, AuditResult] = {}
    for r in rows:
        out.setdefault(r["session_id"], _row(r))
    return out


def delete_session(session_id: str, *, db_path: Path | None = None) -> int:
    path = ensure_db(db_path)
    with connect(path) as conn:
        return conn.execute("DELETE FROM run_audits WHERE session_id = ?", (session_id,)).rowcount
