# Astrowatch 요구사항 정의서

> 명세서 F3001~F3023, NF3001~NF3007 을 천문 관측 도메인으로 옮기고, 구현 위치와 상태를 적는다.
> 갱신일 2026-11-20, 경로는 `backend/astrowatch/` 기준(따로 적은 것 제외)

---

## 1. 도메인 선택 근거

| 조건 | 충족 | 근거 |
|---|---|---|
| 외부 API 3종 이상 | ✅ | 천문연(현상, 출몰, 월령), 기상청(단기, 중기예보), 카카오 로컬 |
| 판단이 필요한 요청 | ✅ | 박명, 월령, 구름, 내 시간을 **겹쳐 봐야** 답이 나온다 |
| 재계획이 의미 있음 | ✅ | 흐리거나 달이 밝으면 다른 날짜, 현상, 지역으로 다시 찾는다 |
| 저장할 결과물 | ✅ | 관측 계획(현상, 날짜, 시작/종료, 장소). 시간 겹침 판단이 자연스럽다 |
| 위험 도메인 | ❌ | 전문가 면책 불필요. 야간 안전 문구 한 줄만 고정 (`safety_note`) |

**LLM 만으로 답하면 틀리는 이유**

| 요청 | LLM 만으로는 |
|---|---|
| "토요일 밤 서울 구름 많아?" | 예보를 모른다 |
| "12/14 유성우, 몇 시부터 어두워져?" | 그날 서울의 천문박명, 월출을 지어낸다 |
| "대전 근처 천문대 20km 안" | 실재 장소와 거리를 모른다 |

**Movie Planner 대응**

| Movie Planner | Astrowatch |
|---|---|
| 박스오피스 (후보) | 천문현상 목록 |
| 영화 상세, 러닝타임 | 출몰, 박명, 월령 (관측 창) |
| 영화관 검색 (보조) | 날씨 예보 + 관측지 검색 |
| 러닝타임 + 광고 ≤ 제약 시각 | 어두운 창 ∩ 내 창 ∩ 현상 창 ∩ 맑은 시간 ≥ 30분 |
| 관람 계획 | 관측 계획 |

---

## 2. 외부 API 목록

| # | API | 정보 | Tool | 형식 | 이용허락 |
|---|---|---|---|---|---|
| 1 | 천문연 천문현상 (15012691) | 월별 현상 `locdate, astroTime, astroEvent` | `search_sky_events` | XML | 제한 없음 |
| 2 | 천문연 출몰시각 (15012688) | 일출몰, 월출몰, 천문박명 `aste/astm` | `get_night_window` | XML | 제한 없음 |
| 3 | 천문연 월령 (15012689) | `lunAge` | `get_night_window` | XML | 제한 없음 |
| 4 | 기상청 단기예보 (15084084) | 시간별 `SKY, POP, PTY` (~3일) | `get_sky_forecast` | JSON | 공공누리 1유형 |
| 5 | 기상청 중기육상예보 | 4~10일 오전/오후 하늘, 강수확률 | `get_sky_forecast` | JSON | 공공누리 1유형 |
| 6 | 카카오 로컬 키워드 검색 | 이름, 주소, 링크, 거리 (반경 ≤ 20km) | `search_observatory` | JSON | 결과 저장 금지 → ID, 링크만 |
| 7 | OpenWeatherMap (선택) | 5일 / 3시간 운량, 강수확률 | `get_sky_forecast` 백업 | JSON | 무료 플랜 |
| 8 | Google Calendar (심화) | 일정 조회, 등록 | `get_calendar`, `add_plan_to_calendar` | JSON | 본인 계정 |

호출 한도는 [README 부록 B](../README.md#부록-b-외부-api-출처와-이용-조건).

---

## 3. 제약이 있는 요청 예시

| # | 요청 | 유형 | 기대 동작 |
|---|---|---|---|
| R1 | "12월 14일 쌍둥이자리 유성우, 서울에서 밤 10시~새벽 2시에 볼 수 있어?" | 한 번에 충족 | **조건부 충족**: 어두운 창, 달 영향 제시, 날씨는 "D-3부터 확인" |
| R2 | "이번 주말 밤 9시~12시 대전에서 볼 만한 거. 구름 많으면 다른 날로" | 재계획 | 현상, 출몰 결과 재사용, 바뀐 날짜만 추가 호출 |
| R3 | "다음 주 서울에서 보름달 피해서 유성 보고 싶어" | 상한 도달 | 재계획 3회 → 멈춤 + "날짜 범위나 지역을 넓힐까요?" |
| R4 | "토요일 밤 대전 근처 천문대, 20km 이내" + 카카오 키 누락 | Tool 실패 | 장소만 `AUTH` 실패, 시간, 날씨 판단은 그대로 |
| R5 | "안녕, 이거 어떻게 써?" | Tool 불필요 | 계획 생략, 사용법 안내 |
| R6 | "같은 날 장소만 강원도로" | 맥락 유지 | 날짜, 시간 유지, 지역만 갱신 |
| R7 | "밤 11시 전엔 들어와야 해" | 되묻기 | Tool 호출 전 "몇 시부터, 어느 지역인가요?" |

조건 분류 (F3005)

| 기준 | 선호 | 제약 |
|---|---|---|
| 날짜(범위), 시작 가능 시각 | 지역, 현상 종류, 현상 이름, 관측지 유형 | 종료 시각, 최대 거리, 구름 허용, 달빛 회피, 고정 조건(`fixed`) |

---

## 4. 저장 결과물

| 저장 | 테이블 | 핵심 값 | 규칙 |
|---|---|---|---|
| 관측 계획 | `plans` | 현상 제목, 종류, `start_dt/end_dt`, 지역, 위경도, `place_id/place_url`, `basis`, `calendar_event_id`, 생성/수정 일시 | 겹쳐도 저장, `overlaps` 로 알림. `end_dt > start_dt` CHECK |
| 실행 로그 | `run_logs` | `(session_id, seq)`, ts, type, stage, tool, payload(JSON) | UPDATE 차단 트리거, 수정 API 없음 |
| 대화 맥락 | `sessions` | 조건 객체, 메모리 요약, 직전 추천, 최근 6턴 | Tool 원본 응답 저장 안 함 |
| 대화 턴 (화면 복원) | `turns` | `(session_id, seq)`, ts, 사용자 메시지, 당시 `ChatResponse` JSON | 저장 실패해도 응답 영향 없음. 대화 삭제 시 `run_logs` 는 유지 |
| 로컬 캘린더 | `local_calendar` | 제목, 시작/종료, `plan_id` | F3023 로컬 구현체 |

스키마: `storage/schema.sql`

---

## 5. 기능 요구사항 (F3001~F3023)

| 번호 | 분류 | 요구사항명 | Astrowatch 구현 내용 | 구현 위치 | 상태 |
|---|---|---|---|---|---|
| F3001 | Tool | Tool 명세 정의, 등록 | 입출력 Pydantic 모델 + 공통 봉투 `{ok, data, note, error}`, 레지스트리 데코레이터, LLM 에는 이름, 설명, 입력 스키마만 | `tools/base.py`, `tools/registry.py`, `tools/contracts.py` | 구현 |
| F3002 | Tool | 후보 조회 | 연, 월 천문현상 → `event_id, date, time, title, kind`. 날짜 미지정 시 실행 흐름이 오늘~+9일 범위로 채우고 응답에 밝힘 | `tools/sky_events.py`, `agent/nodes.py` | 구현 |
| F3003 | Tool | 상세정보 조회 | 후보 날짜 → 일몰, 천문박명, 월출몰, 월령. 박명이 비면 `NO_WINDOW` → 판정 `UNJUDGED` | `tools/night_window.py`, `agent/judge.py` | 구현 |
| F3004 | Tool | 보조 정보 검색 | 시간별 하늘 상태(기상청/OWM), 관측지(카카오). 결과 없음 `NO_RESULT`, 장소와 하늘을 임의로 묶지 않음 | `tools/sky_forecast.py`, `tools/observatory.py` | 구현 |
| F3005 | Agent | 사용자 요청 분석 | LLM structured output → 기준, 선호, 제약, `fixed`. 못 뽑은 값은 null, 존재하지 않는 날짜는 되묻기 | `agent/nodes.py` analyze, `agent/prompts.py` | 구현 |
| F3006 | Agent | 작업 계획 수립 | 의도, 조건 → Tool 순서(규칙, LLM 없음). 인사, 되묻기는 계획 생략 | `agent/nodes.py` build_plan | 구현 |
| F3007 | Agent | Tool 호출, 결과 통합 | 날짜별 펼침 호출, `(tool, args)` 캐시, 실패는 사유 남기고 진행 | `agent/nodes.py` execute | 구현 |
| F3008 | Agent | 제약 적합성 판단 | 창 계산, 3단계 판정, 달, 강수, 천체 고도, 기존 일정. 예보 없으면 "늦어도 ○시 도착" 역산 | `agent/judge.py` | 구현 |
| F3009 | Agent | 재계획 | 추천 0개일 때 ①날짜 ±2일 → ②다른 현상 → ③인접 지역, 상한 3, 캐시 재사용, `stop_reason` 5종 | `agent/replan.py`, `agent/nodes.py` judge | 구현 |
| F3010 | Agent | 대화 맥락 유지 | 조건 병합(말한 값만 갱신), 요약, 직전 추천, 최근 6턴을 세션 테이블에. 이전 조건과의 차이(`changes_from_previous`)를 응답에 반영. 화면 복원은 `turns` 표 + `/api/sessions` | `agent/memory.py`, `storage/sessions.py`, `storage/turns.py`, `app/api/sessions.py`, `schemas/domain.py` | 구현 |
| F3011 | Agent | 최종 응답 | 추천, 관측 창, 맑은 시간, 월령, 역산, 제외 사유, 근거 Tool, 판단 기준, 안전 문구 | `agent/nodes.py` respond, `agent/prompts.py` | 구현 |
| F3012 | 로깅 | 실행 이벤트 발행 | 단계 진입/종료, Tool 호출/응답(소요 ms), LLM 판단, 재계획 등 기본 9종 + 심화 5종(`planner_tasks`, `worker_result`, `worker_decision`, `audit_result`, `audit_diagnosis`). 구독자 무관 | `events/bus.py`, `agent/nodes.py` stage | 구현 |
| F3013 | 로깅 | 실행 로그 저장 | 로깅 Agent 가 구독 → `run_logs` 에 덧붙임. LLM 없음, 저장 실패는 삼킴 | `logging_agent/agent.py`, `storage/run_logs.py` | 구현 |
| F3014 | 로깅 | 로그 조회, 삭제 | 세션 목록, 상세(seq 순), 삭제, 목록 `limit/offset`. 수정 API 없음(PATCH 405) + DB 트리거 | `app/api/logs.py`, `storage/schema.sql` | 구현 |
| F3015 | 계획 | 계획 저장, 조회 | 저장 + 같은 시간대 겹침 `overlaps` 반환(한 트랜잭션) | `storage/plans.py`, `app/api/plans.py` | 구현 |
| F3016 | 계획 | 계획 수정, 삭제 | 보낸 필드만 PATCH, 삭제, 화면 목록 즉시 반영 | `storage/plans.py`, `frontend/js/plans.js` | 구현 |
| F3017 | 서비스 | FastAPI | `/api/chat`, `/api/plans`, `/api/logs`(판정 3개 포함), `/api/sessions`, `/api/calendar`, `/api/sky`, 메타 분리(21개 엔드포인트, `@router.` 기준), Pydantic 요청/응답, 보안 미들웨어(요청 가드, 보안 헤더, TrustedHost, CORS) | `app/main.py`, `app/api/*.py`, `app/security.py`, `schemas/api.py` | 구현 |
| F3018 | 서비스 | 프론트엔드 | 대화, 계획 관리, 실행 로그, 하늘 가리키기 4화면 (HTML/CSS/JS, `index`, `plans`, `logs`, `pointer`), 좌측 레일 최근 대화(세션 복원), 모바일 하단 탭, 실행 로그 화면의 로깅 Agent 판정 배지와 패널, 인라인 스크립트 없음(CSP) | `frontend/` | 구현 |
| F3019 | 서비스 | 실행 과정, 상태 표시 | 타임라인(단계, Tool, 재계획 회차 구분), 로딩, 종료 사유 배너, 키 누락/실패 경고, 카드 관측 창 헤드라인, 밤 타임바(관측 창, 하늘, 달, 날씨, 천체 행 분리) | `frontend/js/timeline.js`, `frontend/js/chat.js`, `frontend/js/ui.js` | 구현 |
| F3020 | 심화 | 이상 징후 판단 | 규칙 판정 3종(같은 입력 반복 호출, 재계획 과다, 계획 외 Tool 호출), 근거 없으면 판정 불가, 턴 종료 뒤 자동 판정 + `state["audit"]`/`ChatResponse.audit` 로 메인 Agent 참조, 이상일 때만 LLM 진단(판정과 분리 저장), 판정 API 3개, 실행 로그 화면 배지와 판정 패널 | `logging_agent/audit.py`, `logging_agent/diagnosis.py`, `storage/run_audits.py`, `agent/service.py`, `app/api/logs.py`, `scripts/make_anomalies.py` | 심화 구현 |
| F3021 | 심화 | Planner, Worker 분리 | `AGENT_STRUCTURE=planner_worker`(기본): analyze → planner → worker → judge → verify → respond. Planner 가 `TaskList`(task_id, fixed_args, depends_on, trigger user/judge_replan/worker_request/audit_anomaly)를 발행, Worker 가 작업마다 Tool 호출 후 `WorkerResult` 발행. Worker LLM 은 NO_RESULT, NOT_FOUND, 형식 오류일 때만(재시도는 같은 Tool, `WORKER_MAX_RETRY` 안, 고정 인자 불변), 치명적 실패, 필수 인자 누락, 모호함은 규칙. 해결 못 한 실패는 Planner 재계획으로, verify(로깅 Agent 규칙 판정)가 이상이면 턴마다 한 번 재구성. 전후 비교 `scripts/compare_structures.py` → `samples/structure_compare.json`, `ChatResponse.llm_calls` | `agent/planner.py`, `agent/worker.py`, `agent/verify.py`, `agent/graph.py`, `agent/service.py`, `scripts/compare_structures.py`, `tests/test_planner_worker.py` | 심화 구현 |
| F3022 | 심화 | RAG 검색 Tool | 한국어 위키백과 extract 321건(카탈로그 294 + 용어 27)을 문단 청크 724개로 Chroma 에 색인(로컬 ONNX all-MiniLM-L6-v2, 384차원, `EMBEDDING_PROVIDER=local` 기본), `search_space_docs` 로 하이브리드 검색(이름 가산 + 벡터). 배경 지식 질문(정의, 원리, 역사)은 analyze 가 knowledge 로 분류해 이 Tool 만 호출, 답 끝에 "출처:" 줄(제목, URL). 색인 없음은 `NO_INDEX` → "아직 문서를 모으지 못했습니다(색인 없음)." + 색인 안내, 임베딩 불일치는 `INDEX_MISMATCH` | `rag/`, `tools/space_docs.py`, `agent/nodes.py`, `agent/prompts.py`, `scripts/collect_space_docs.py`, `scripts/build_space_docs.py`, `tests/test_tools_space_docs.py` | 심화 구현 |
| F3023 | 심화 | 캘린더 연동 Tool | 입출력 계약 → 로컬 SQLite 구현체 → Google 교체. 등록은 `plan_id` 만, 조회 일정은 판정에 반영 | `tools/calendar.py`, `clients/calendar_provider.py`, `agent/judge.py` | 심화 구현 |

심화 4건(F3020~F3023) 모두 구현. 확인 근거는 [VERIFICATION 6절](VERIFICATION.md#6-46-심화-f3020f3023).

### 5-1. 추가 기능 (명세 외)

| 이름 | 내용 | 구현 위치 | 상태 |
|---|---|---|---|
| 하늘 가리키기 | 저장한 계획의 천체(카탈로그 천체, 행성, 달, 유성우 복사점 12종) 쪽으로 휴대폰을 안내. 방향 센서(DeviceOrientation), 현재 위치(Geolocation), 센서가 없으면 마우스, 방향키 시뮬레이션. 계획 화면 "하늘에서 찾기" 버튼, 계획에 `target_object` 열(기존 계획은 제목에서 한 번 추론) | `astro/targets.py`, `data/meteor_radiants.json`, `app/api/sky.py` (`GET /api/sky/target`, `GET /api/sky/targets`), `storage/plans.py`, `frontend/pointer.html`, `js/pointer.js`, `js/skymath.js` | 구현 (테스트 `test_sky_targets.py` 42, `test_api_sky.py` 20, `test_frontend_skymath.py` 12, 화면 22, 23) |

---

## 6. 비기능 요구사항 (NF3001~NF3007)

| 번호 | 분류 | 요구사항명 | Astrowatch 구현 내용 | 구현 위치 | 상태 |
|---|---|---|---|---|---|
| NF3001 | 보안 | API 키 관리 | `.env` → `Settings`. 소스에 키 없음, `.env`, `secrets/` gitignore. 로그 키 리댁션, Google 토큰 0600 원자 저장, 심볼릭 링크 거부, 설정 경로를 저장소 안으로 제한(`ALLOW_EXTERNAL_PATHS`) | `config.py`, `redact.py`, `clients/calendar_provider.py`, `.env.example`, `.gitignore` | 구현 |
| NF3002 | 보안 | 화면 노출 제한 | Tool 오류 메시지에 키, URL 금지, 경고는 고정 문구, 미처리 예외는 "서버 내부 오류", 검증 오류에 입력값 미반영, 답변에 내부 필드명 금지. CORS 허용 출처 한정, CSRF 3중(Origin, Sec-Fetch-Site, `X-Requested-With`), 보안 헤더, CSP, 선택형 `APP_ACCESS_TOKEN`, TrustedHost | `tools/base.py`, `agent/service.py`, `app/errors.py`, `app/security.py`, `agent/prompts.py` | 구현 |
| NF3003 | 안정성 | Tool 실패 처리 | 모든 예외를 봉투로 변환(`INTERNAL`), 실행은 계속, `run_turn` 은 항상 응답 | `tools/base.py` invoke, `agent/service.py` | 구현 |
| NF3004 | 안정성 | 재계획 상한 | `MAX_REPLANS=3`, 그래프 `recursion_limit=60` | `config.py`, `agent/nodes.py`, `agent/service.py` | 구현 |
| NF3005 | 성능 | 응답 시간 | HTTP 타임아웃 15초, 외부 응답 2MB 상한, LLM 60초, 한 번에 날짜 10개 상한, 같은 인자 재호출 안 함, `/api/chat` IP 당 분당 12회(429), 요청 본문 256KB, 목록 최대 1000건 | `clients/http.py`, `agent/llm.py`, `agent/nodes.py`, `app/security.py`, `app/api/__init__.py` | 구현 (부하 시험 미실시) |
| NF3006 | 구조 | 모듈 분리 | `frontend/` ↔ `backend/`, agent, tools, clients, events, logging_agent, storage 분리 | 저장소 구조 | 구현 |
| NF3007 | 법/윤리 | 출처와 이용 조건 | README 출처, 한도, 약관 표. 카카오 결과는 ID, 링크만 저장 | `README.md`, `schemas/plan.py` | 구현 |

---

## 7. 기획 검증 결과

### 7-1. 외부 API 호출 확인 (2026-11-18)

| 확인 | 결과 |
|---|---|
| data.go.kr 5개 서비스 | 활용신청 완료, 실제 호출 응답 |
| 천문현상 2026-12 | 16건. 12/14 23:00 쌍둥이자리 유성우 극대(ZHR 150) |
| 출몰 서울 12/14 | 천문박명 종료 18:48, 시작 06:05, 월몰 21:52 |
| 월령 12/14 | 5.5 → R1 달 방해 없음 |
| 단기, 중기예보 | 단기 시간별 SKY 69건, 중기 3~10일 오전/오후 |
| 카카오 (대전 20km "천문대") | 5건 (대전시민천문대 4.4km 등) |
| OWM | 5일 / 3시간 40슬롯 |
| GMS Tool 선택 | `search_sky_events(2026, 12)` 선택: 당시 `gpt-6-luna`, `reasoning_effort="none"` 필수 |
| GMS 경로 전환 (11-20) | OpenAI 경로(`gpt-6-luna`) 사용 불가 → `LLM_PROVIDER=anthropic`(기본), `claude-haiku-5-5`, Anthropic 호환 경로, `x-api-key` 헤더, 1회 1크레딧, structured output 은 function_calling. 샘플 11종 재캡처로 확인 |
| Open-Meteo | **차단** → 백업에서 제외 |
| 한국어 위키백과 (F3022, 11-20) | MediaWiki API 로 제목 확인(50개씩) 후 도입부 extract 를 20개씩 묶어 수집. User-Agent 명시, 1초 간격, `maxlag=5`. 결과 `data/space_docs.json` 321건, 누락 0 |

F3022 문서 출처

| 출처 | 호출 | 이용 조건 | 호출 예절 | 저장 |
|---|---|---|---|---|
| 한국어 위키백과 MediaWiki API (`ko.wikipedia.org/w/api.php`) | `action=query`, `prop=extracts`, `explaintext`, 리다이렉트 따라감 | CC BY-SA 4.0 (출처 표시, 동일 조건 변경 허락) | User-Agent `Astrowatch/0.1 (SSAFY project; ...)`, 요청 하나씩 1초 간격(`REQUEST_INTERVAL`), `maxlag`/429 는 Retry-After(없으면 15~120초)만큼 대기 | 문서 앞부분 extract 만 (문서당 ≤ 6,000자, 파일 ≤ 약 2MB), 제목과 원문 URL 함께 저장, 답변에 출처 표시 |
| chromadb ONNX 모델 (all-MiniLM-L6-v2) | 최초 1회 내려받아 `~/.cache/chroma` 에 둠 | Apache 2.0 | | 색인은 `data/chroma` (gitignore) |

원본 응답: `samples/kasi_*.xml`, `samples/kma_*.json`, `samples/kakao_*.json`, `samples/owm_*.json`, `samples/gms_*.json`

### 7-2. Tool 출력 구조 대조

| 응답 특성 | 반영 |
|---|---|
| `astroTitle` 대부분 빈 값, 이름은 `astroEvent` | `title = astroTitle or astroEvent` |
| 월 요약 행 `locdate` 6자리 | 8자리만 후보 (테스트: 16건 중 15건) |
| 출몰 시각 `HHMM` 문자열 | 분 단위로 파싱 |
| 중기예보 발표 시각별 필드 차이 | 밤은 `Pm`, 8~10일은 하루 값 |
| Encoding 키 혼입 | `%` 포함 시 unquote |

### 7-3. 설계 시험과 설계 변경

| 시험, 발견 | 설계 변경 |
|---|---|
| 12/14 은 예보 밖 → 충족/불충족만으로는 답이 없음 | 판정 **3단계**(충족, 조건부 충족, 불충족) + 판단 안 함(`UNJUDGED`) |
| 카카오로 지오코딩하면 키 누락 시 박명, 날씨까지 실패 | 지역 표 **로컬화** (`data/regions.py`, 위경도, 격자, 중기 구역코드) |
| "안드로메다 은하", "목성" 은 현상 API 에 없음 | **천체 카탈로그 294개** + 행성 계산 → 고도 20° 이상 구간만 관측 창 (`lookup_sky_object`) |
| "보기 좋은 날 추천해 줘" 가 되묻기로 막힘 | 날짜는 필수 아님 → **기본 범위 오늘~+9일** (중기예보 한도) |
| E2E: 미등록 천체를 충족으로 추천 | 대상 미확인이면 `UNJUDGED` + 비슷한 이름 되묻기 |
| E2E: 2월 30일을 날짜 미지정으로 처리 | `date_invalid` → 외부 조회 없이 되묻기 |
| E2E: 재계획 0회인데 "상한 도달" | `stop_reason` 5종 분리, 상한 배너는 실제 도달 때만 |
| E2E: 자정 넘김 한 밤을 이틀로 해석 | LLM `single_night` 일 때만 한 밤으로 접기 |

E2E 리뷰 상세: [VERIFICATION.md](VERIFICATION.md)
