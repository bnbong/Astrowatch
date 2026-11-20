-- Astrowatch SQLite 스키마 (F3013 실행 로그 · F3015 계획)

CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    event_title     TEXT    NOT NULL,
    event_kind      TEXT    NOT NULL DEFAULT 'other',
    start_dt        TEXT    NOT NULL,           -- ISO 8601, 현지 naive
    end_dt          TEXT    NOT NULL,
    location_label  TEXT    NOT NULL,
    lat             REAL,
    lon             REAL,
    place_id        TEXT,                       -- 카카오: ID·링크만 저장 (약관)
    place_url       TEXT,
    target_alt      REAL,
    target_az       REAL,
    target_object   TEXT,                       -- 하늘 가리키기용 천체 키(카탈로그 이름, 행성, 유성우 복사점). 없으면 NULL
    basis           TEXT,
    calendar_event_id TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    CHECK (end_dt > start_dt)
);
CREATE INDEX IF NOT EXISTS idx_plans_start ON plans(start_dt);

-- 실행 로그: 세션 단위, 발생 순서(seq). 수정 API 없음 — UPDATE 를 막는 트리거를 둔다.
CREATE TABLE IF NOT EXISTS run_logs (
    session_id  TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    ts          TEXT    NOT NULL,
    type        TEXT    NOT NULL,
    stage       TEXT,
    tool        TEXT,
    payload     TEXT    NOT NULL DEFAULT '{}',  -- JSON
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_run_logs_ts ON run_logs(ts);

CREATE TRIGGER IF NOT EXISTS run_logs_immutable
BEFORE UPDATE ON run_logs
BEGIN
    SELECT RAISE(ABORT, 'run_logs is append-only');
END;

-- 대화 맥락 (F3010): 조건 객체 · 직전 추천 요약만. Tool 원본은 저장하지 않는다.
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    conditions_json TEXT    NOT NULL DEFAULT '{}',
    memory_summary  TEXT,
    last_recommended_json TEXT,                 -- 직전 추천 제목 JSON 목록 (옛 행은 후보 전체 JSON, 제목만 읽음)
    history_json    TEXT    NOT NULL DEFAULT '[]',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- 대화 턴 (화면 복원용): 사용자 메시지와 구조화 응답(ChatResponse JSON)을 턴 단위로 보관.
-- LLM 맥락은 sessions.history_json(최근 6턴)이 맡고, 이 표는 화면 복원·세션 목록에만 쓴다.
CREATE TABLE IF NOT EXISTS turns (
    session_id      TEXT    NOT NULL,
    seq             INTEGER NOT NULL,
    ts              TEXT    NOT NULL,
    user_message    TEXT    NOT NULL,
    response_json   TEXT    NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);

-- 심화 F3023 로컬 캘린더 구현체
CREATE TABLE IF NOT EXISTS local_calendar (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    start_dt    TEXT NOT NULL,
    end_dt      TEXT NOT NULL,
    plan_id     INTEGER,
    created_at  TEXT NOT NULL
);

-- 심화 F3020: 로깅 Agent 의 이상 징후 판정. 턴(세션 내 seq 구간) 단위. 판정(규칙)과 진단(LLM)은 분리해 저장한다.
CREATE TABLE IF NOT EXISTS run_audits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    seq_from        INTEGER NOT NULL,
    seq_to          INTEGER NOT NULL,
    status          TEXT    NOT NULL,           -- normal | anomaly | undetermined
    findings_json   TEXT    NOT NULL DEFAULT '[]',
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    rule_version    TEXT    NOT NULL DEFAULT '1',
    audited_at      TEXT    NOT NULL,
    diagnosis_json  TEXT,                       -- 이상 판정 실행에만. 실패해도 판정은 남는다
    UNIQUE (session_id, seq_from, seq_to)
);
CREATE INDEX IF NOT EXISTS idx_run_audits_session ON run_audits(session_id, seq_to);
