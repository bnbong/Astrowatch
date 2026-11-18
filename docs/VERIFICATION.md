# Astrowatch 검증 결과

> 명세 4장 확인 항목 · 대표 시나리오 R1~R7 · 실제 E2E 리뷰 발견과 수정을 근거 파일과 함께 정리한다.
> 갱신일 2026-11-18 · 테스트 경로는 `backend/tests/`

---

## 0. 근거의 종류

| 표시 | 뜻 | 근거 위치 |
|---|---|---|
| **E2E** | 실행 중인 서버에 실제 HTTP 요청 · 실제 LLM · 실제 외부 API (Codex 리뷰, 2026-11-18 15시경) | `output/e2e-review/Sxx.json`, `Sxx-logs.json`, `browser-*.png` |
| **실측 샘플** | 실제 키로 1회 실행한 응답 저장 | `samples/run_R1.json`, `run_R4_nokakao.json`, `run_andromeda.json`, `run_fact_mars.json` |
| **mock 샘플** | 실제 LLM · 천문연, 날씨만 `WEATHER_PROVIDER=mock`(전부 흐림) | `samples/run_R2_mock.json`, `run_R3_mock.json` |
| **자동 테스트** | 키 · 네트워크 없이 `samples/` fixture · 가짜 LLM | `backend/tests/*.py` |
| **미검증** | 아직 실행 증거 없음 | — |

> ⚠️ E2E 리뷰와 `samples/run_*.json` 은 **6절의 수정 이전** 코드로 실행했다. 수정 후 동작은 자동 테스트로만 확인했고, 실제 E2E 재실행은 3일차 예정(미검증).

**자동 테스트**: 18개 파일 · 233개 · 전부 통과 (`..\.venv\Scripts\python -X utf8 -m pytest`, 2026-11-18). 리뷰 시점은 181개였다(`output/e2e-review/pytest.txt`).

---

## 1. 4.1 Tool 확인 (F3001~F3004)

| 확인 항목 | 방법 | 근거 | 결과 |
|---|---|---|---|
| Tool 이름 · 설명 · 입력 스키마 목록 | `GET /api/tools` | E2E `meta-tools.json` (당시 5종) · `test_tools_is_list` · `test_openai_tool_schema_has_only_name_desc_params` | ✅ (현재 7종 — 재캡처 필요) |
| 정상 · 실패 호출이 봉투로 반환 | Tool 별 정상 / 키 없음 / 잘못된 입력 / 타임아웃 | `test_exception_becomes_envelope_not_raise` · `test_bad_input_and_unknown_tool` · `test_*_no_key_is_auth*` · `test_timeout_mapping` · `test_radius_over_limit_is_bad_input` | ✅ 자동 테스트 |
| 기준 날짜 미지정 시 기본값 | 날짜 없이 요청 | 실측 `run_andromeda.json` ("2026-11-18~2026-11-27 범위에서 찾았습니다") · `test_deep_sky_without_date_uses_default_range` | ✅ |
| 요청 기준 ≠ 조회 기준을 밝힘 | 예보 밖 날짜 · 중기예보 · mock | 실측 `run_R1.json` 사유 "예보 범위 밖(D-3부터 확인 가능)" · `test_kma_mid` note "시간별 예보 아님" · mock note | ✅ |
| 후보 식별자 → 상세 조회로 이어짐 | 현상 date → 박명 date | `run_R1.json` seq 8→10 · `test_r1_flow` (Tool 순서 고정) | ✅ |
| 판단 속성이 비면 판단 안 함 | 박명 빈 값 · 미등록 천체 | `test_blank_twilight_is_no_window` · `test_no_night_window_unjudged` · `test_object_not_found_is_unjudged` · `test_r1_unknown_object_not_recommended` | ✅ 수정 후 (E2E S07 은 수정 전 실패 → 6절 R1) |
| 보조 Tool 결과 없음 형식 | 반경 안 0건 | `test_no_result` (observatory) | ✅ 자동 테스트 |

---

## 2. 4.2 메인 Agent 실행 흐름 (F3005~F3011)

| 확인 항목 | 방법 | 근거 | 결과 |
|---|---|---|---|
| 조건 추출 · 임의로 안 채움 | `ChatResponse.conditions` | E2E S03 · S02(지역 · 시작 시각 null 로 되묻기) · `test_conditions_missing_rules` | ✅ |
| 그래프 5노드 | `GET /api/graph` | E2E `meta-graph.json` · `test_graph` · `test_r1_flow` (단계 순서) | ✅ |
| Tool 필요 / 불필요 계획이 다름 | 인사 vs 관측 요청 | E2E S01(Tool 0) vs S03(Tool 3) · `test_greeting_no_tools` | ✅ |
| 앞 결과 → 다음 입력 · 한 Tool 실패에도 진행 | 카카오 키 제거 | E2E S20 · 실측 `run_R4_nokakao.json` · `test_tool_auth_failure_continues` | ✅ |
| 계산 값과 응답 판단 기준 일치 | 관측 창 · 맑은 시간 | `run_R1.json` 사유 240분 = 답변 22:00~02:00+1 · `test_judge.py` 26건 | ✅ |
| 확인 못 하는 값은 역산 기준 | 예보 밖 | `run_R1.json` "늦어도 01:30+1 도착" · `test_partial_latest_arrival_skips_cloudy` | ✅ |
| 재계획 시 이미 받은 결과 재호출 안 함 | 흐림 → 재계획 | `test_replan_reuses_cache` · mock `run_R2_mock.json` "캐시 재사용 1회 / 4회" · E2E S08 (Tool 호출 12개 모두 고유) | ✅ |
| 상한 도달 시 멈춤 + 사유 | 낮 시간 유성우 · mock 흐림 | E2E S08(3회) · mock `run_R3_mock.json` · `test_replan_limit` (`stop_reason=limit_reached`) | ✅ |
| 후속 요청은 바뀐 조건만 | "장소만 대전으로" · "종료만 23시로" | E2E S14 · S15 · `test_follow_up_keeps_context` | ✅ |
| 추천 · 보조 정보 · 기준 · 근거 · 제외 사유 | 응답 구성 | `run_R1.json` answer · mock `run_R2_mock.json` 제외 8건 사유 | ✅ |
| 위험 도메인 안내 | — | 해당 없음 (야간 안전 문구만) | 해당 없음 |

---

## 3. 4.3 로깅 Agent · 실행 로그 (F3012~F3014)

| 확인 항목 | 근거 | 결과 |
|---|---|---|
| 단계 진입/종료 · Tool 요청/응답 · 소요 시간 이벤트 | `run_R1.json` steps · E2E `S03-logs.json` | ✅ |
| LLM 판단과 이유가 이벤트에 | `llm_decision` (`run_R1.json` seq 3) | ✅ |
| 로깅 Agent 가 LLM 을 안 씀 | `test_no_llm_dependency` | ✅ 자동 테스트 |
| 로깅 Agent 없이도 메인 정상 | `test_runs_without_subscribers` · `test_bad_db_path_never_raises` | ✅ 자동 테스트 (운영 중 분리 실험은 미검증) |
| 세션별 구분 · 섞이지 않음 | `test_events_in_seq_order_and_isolated` · `test_seq_continues_after_restart` | ✅ |
| 목록 · 상세(순서) · 삭제 | E2E 목록·상세 200, 삭제 204 → 404 · `test_logs_list_detail_delete` | ✅ |
| 수정 API 없음 | E2E PATCH 405 · `test_logs_have_no_update_api` · `test_update_blocked_by_trigger` · `test_no_update_function` | ✅ |

---

## 4. 4.4 계획 관리 (F3015~F3016)

| 확인 항목 | 근거 | 결과 |
|---|---|---|
| 저장 · 목록 · 상세에 핵심 값 + 생성 일시 | E2E `management.json` · 브라우저 `browser-plan.png` · `test_crud_roundtrip` | ✅ |
| 겹치는 계획: 저장 + 알림 | E2E `management.json` (201, overlaps 1건) · `test_overlap_detected_but_not_blocking` | ✅ |
| 수정 · 삭제가 목록에 바로 반영 | E2E 브라우저 수정 · 삭제 · 역전 시각 422 · `test_plans_crud` · `test_plans_validation` | ✅ |

---

## 5. 4.5 서비스 구성 · 화면 (F3017~F3019)

| 확인 항목 | 근거 | 결과 |
|---|---|---|
| 대화 · 계획 · 로그 API 경로 분리, 형식 일치 | E2E `openapi.json` · `browser-openapi.png` | ✅ |
| 두 화면 모두 백엔드 호출 | E2E 브라우저 (Edge headless, pageerror 0) · `browser-chat.png` · `browser-plan.png` | ✅ |
| 실행 단계 · Tool 이력 순서대로, 재계획 지점 구분 | `StepOut.replan_round` · `frontend/js/timeline.js` · `test_replan_reuses_cache` | ✅ (재계획 화면 캡처는 미첨부) |
| 처리 중 표시 · Tool 실패 · 키 누락 안내 | E2E 로딩 표시 확인 · S20 warning | ✅ API / **AUTH 배너 브라우저 캡처 미검증** |

---

## 6. 4.6 심화 — F3023 캘린더 (선택 과제만)

| 확인 항목 | 근거 | 결과 |
|---|---|---|
| 로컬 저장소 구현체로 조회 · 등록 | `test_local_add_then_query` · `test_register_then_query_local` · `test_duplicate_registration_is_blocked` | ✅ 자동 테스트 |
| Google 로 바꿔도 Tool 입출력 동일 | `test_switching_provider_keeps_tool_io` · `test_google_insert_args` · `test_google_list_args_and_parsing` (Google 클라이언트 가짜 객체) | ✅ 자동 테스트 |
| 조회 일정이 판정에 반영 | `test_calendar_conflict_shrinks_window` · `test_calendar_full_overlap_unfit` · `test_calendar_auth_failure_continues` | ✅ 자동 테스트 |
| 실제 Google 계정 등록 | — | **미검증** (토큰 발급 여부 미확인) |

F3020 · F3021 · F3022 는 선택하지 않았거나 미구현이라 확인 대상이 아니다. E2E S12(배경지식)는 "미지원" 안내.

---

## 7. 4.7 대표 시나리오

| # | 요청 | 기대 | 실제 | 근거 | 종류 | 차이 |
|---|---|---|---|---|---|---|
| R1 | 12/14 서울 22~02시 쌍둥이자리 유성우 | 조건부 충족 · 달 영향 · "D-3부터" | 조건부 충족, 창 240분, 월령 5.5, 늦어도 01:30+1 | `samples/run_R1.json` · E2E S03 | 실측 · E2E | 없음 |
| R2 | 주말 21~24시 대전, 흐리면 다른 날 | 재계획 + 현상 · 출몰 재사용 | 재계획 3회(날짜 → 현상 → 세종) 후 추천 0. 회차마다 캐시 재사용 | `samples/run_R2_mock.json` | mock | mock 은 모든 날 흐림이라 재계획 후 충족이 불가능 → 상한까지 감. "재계획 후 충족" 경로는 `test_replan_reuses_cache` 로 확인 |
| R3 | 다음 주 서울 보름달 피해 유성 | 재계획 3회 → 멈춤 + 넓힐지 질문 | 3회(날짜 → 현상 → 인천) 후 "날짜 범위나 지역을 더 넓혀 볼까요?" | `samples/run_R3_mock.json` · E2E S08 | mock · E2E | 없음 (당시 `stop_reason` 필드 없음) |
| R4 | 토요일 대전 천문대 20km + 카카오 키 없음 | 장소만 실패, 판단은 진행 | 장소 `AUTH`, 경고 1건, 수성 최대이각 충족 | `samples/run_R4_nokakao.json` · E2E S20 | 실측 · E2E | 없음 |
| R5 | 안녕, 이거 어떻게 써? | Tool 없이 사용법 | chat, Tool 0, 6.58초 | E2E S01 · `test_greeting_no_tools` | E2E | 없음 |
| R6 | 같은 날 장소만 바꿔 | 날짜 · 시간 유지, 지역만 | 대전으로만 바뀜, 현상 조회 재사용(박명 · 예보만 호출) | E2E S14 · `test_follow_up_keeps_context` | E2E | 기획 문구는 "강원도", 실제 시험은 "대전" |
| R7 | 밤 11시 전엔 들어와야 해 | Tool 전 되묻기 | clarify, "어느 지역 · 몇 시부터" | E2E S02 · `test_clarify_when_only_end_time` | E2E | 없음 |

보충 실측: `samples/run_andromeda.json`(날짜 미지정 · 천체 고도 · 달 회피 · 충족 3건), `samples/run_fact_mars.json`(FACT · 행성 최적 시각).
요청 원문은 `samples/run_*.json` 에 저장되지 않았다 — E2E 파일에는 `message` 가 있다.

**대표 요청 1건 전체 로그 · 재계획 사례**: [RUN_LOG_SAMPLE.md](RUN_LOG_SAMPLE.md)

---

## 8. 실제 E2E 리뷰 발견과 수정

리뷰: `output/e2e-review/REVIEW.md` (Codex, S01~S20 실제 요청 · Edge 브라우저). 수정 내용은 현재 코드 · 테스트에서 확인했다.

| ID | 발견 (리뷰 당시) | 근거 | 수정 | 확인 테스트 |
|---|---|---|---|---|
| R1 · P1 | 카탈로그에 없는 천체를 `fit` 추천 | S07 | 대상 미확인이면 `UNJUDGED` · 재계획 안 함 · 비슷한 이름 되묻기 · 일시 오류와 구분 (`judge.py` `TARGET_NOT_FOUND`, `nodes.py` `unresolved_answer`) | `test_r1_unknown_object_not_recommended` · `test_r1_similar_names_in_reason_and_question` · `test_r1_temporary_error_distinguished` · `test_r1_general_request_keeps_general_candidate` |
| R2 · P1 | 2월 30일을 날짜 미지정으로 처리 → 10일 탐색 · 34.84초 | S11 | `date_invalid` 필드 + 파싱 실패를 모아 `date_problem` → 외부 조회 없이 되묻기, 이전 날짜 숨김 | `test_r2_invalid_date_asks_back` · `test_r2_invalid_date_does_not_silently_reuse_previous` |
| R3 · P2 | 재계획 0회인데 "상한(0회) 도달" | S13 · S05 | `stop_reason` 5종 · `replan_limit` 분리 · `replan_limit_reached` 는 실제 도달 때만 · 화면 배너를 사유별로 (`frontend/js/chat.js`) | `test_r3_never_rises_is_target_unobservable` · `test_r3_fixed_constraints` · `test_r3_stop_reason_none_when_recommended` · `test_replan_limit` |
| R4 · P2 | 자정 넘김 한 밤을 이틀로 | S10 | LLM `single_night` 필드 · 그때만 한 밤으로 접기 | `test_r4_overnight_single_night_folded` · `test_r4_to_conditions_update_unit` |
| R5 · P2 | 없는 캘린더 기능으로 유도 | S16 | F3023 구현(Tool · API · 화면 버튼) + Tool 없으면 "제공하지 않음" 정직 안내 · "계획 N번" 추출 | `test_r5_calendar_unavailable_is_honest` · `test_r5_calendar_registers_plan_by_number` · `test_r5_calendar_guide_without_number` · `test_r5_extract_plan_id` · `test_r5_knowledge_without_docs_tool` |
| R6 · P2 | 제출 문서 미완성 | — | README · docs 6종 작성 (이 문서 포함) | — |
| R7 · P3 | 공백 입력이 LLM 호출 | S18 | `ChatRequest` 에서 trim 후 비면 422 | `test_chat_whitespace_only_message_is_422` · `test_chat_message_is_trimmed_before_run_turn` |
| R8 · P3 | Markdown 이 그대로 노출 | `browser-chat.png` | 이스케이프 먼저 하는 안전한 Markdown 부분집합 렌더러 (`frontend/js/markdown.js`) | `test_frontend_markdown.py` 7건 (node 로 실행) |
| S09 | 장소만 물었는데 관측 계획까지 | S09 | 현상 · 시간 · 관측 의사 없으면 `fact/places` 로 (`places_only`) + 프롬프트 예시 | `test_s09_places_only_demoted_to_fact` · `test_s09_observation_wish_stays_plan` |

**수정 후 실제 E2E 재실행**: 미검증 → 3일차 S03 · S07 · S10 · S11 · S13 · S16 · S18 재실행 예정.

E2E 응답 시간 (S01~S19 성공 17건): 중앙값 6.30초 · 최소 2.09초 · 최대 34.84초(S11, R2 수정으로 외부 조회가 사라짐). 부하 시험은 하지 않았다.

---

## 9. 4.8 비기능 요구사항

| 번호 | 확인 | 근거 | 결과 |
|---|---|---|---|
| NF3001 | 소스 · 제출물에 키 없음 | `.gitignore` 의 `.env` · `secrets/` (`git check-ignore`), 추적 파일에 없음, 키는 `config.py` 가 환경변수로만 읽음 | ✅ (Git 전체 이력 감사는 미검증) |
| NF3002 | 화면에 키 · 오류 상세 없음 | E2E S20 경고 문구 · `test_chat_exception_hides_details` · `test_calendar_auth_failure_continues` (토큰 문구 숨김) | ✅ |
| NF3003 | Tool 실패에도 서비스 계속 | E2E S20 · `test_tool_auth_failure_continues` · `test_exception_still_returns_response` | ✅ |
| NF3004 | 재계획 상한 | E2E S08 · `test_replan_limit` · `recursion_limit=60` | ✅ |
| NF3005 | 처리 시간 | E2E 중앙값 6.30초 · 최대 34.84초 | 관측값만 (SLA 미정 · 부하 시험 미검증) |
| NF3006 | 모듈 분리 | `frontend/` ↔ `backend/`, `agent/` · `tools/` · `clients/` · `events/` · `logging_agent/` · `storage/` | ✅ |
| NF3007 | 출처 · 이용 조건 기록 | [README 5절](../README.md#5-외부-api-출처--이용-조건-nf3007) · 계획엔 카카오 ID · 링크만 | ✅ 기록 (약관 준수 전반은 미검증) |

---

## 10. 4.9 제출 전 점검 목록

| 항목 | 상태 |
|---|---|
| 필수 F3001~F3019 동작 | ✅ 자동 테스트 · E2E (수정 후 E2E 재실행 필요) |
| 선택 심화 동작 · 필수 결과와 비교 | F3023 자동 테스트 ✅ · 실제 Google 미검증 · 비교 표 미작성 |
| 스크린샷 · 실행 로그 첨부 | 일부 (`output/e2e-review/`) · 아래 자리표시자 채울 것 |
| 문서 7종 + README | ✅ (`docs/` 6종 + 기획안 + README) |
| 셀프 체크리스트 | ⏳ 3일차 오후 제공 예정 |

---

## 11. 스크린샷 자리표시자 (3일차에 추가)

`docs/screenshots/` 에 아래 이름으로 넣는다.

| 파일명 | 확인 항목 |
|---|---|
| `4-1_tools.png` | `GET /api/tools` 출력 (7종) |
| `4-2_graph.png` | `GET /api/graph` 그래프 |
| `4-2_r1_answer.png` | R1 대화 · 조건부 충족 카드 |
| `4-2_replan_timeline.png` | 재계획 회차가 구분된 타임라인 (mock) |
| `4-2_limit_banner.png` | 상한 도달 배너 (`limit_reached`) |
| `4-2_followup.png` | 후속 요청 (장소만 변경) |
| `4-3_logs_list.png` · `4-3_logs_detail.png` | 실행 로그 목록 · 상세 |
| `4-4_plan_overlap.png` | 겹침 알림 |
| `4-4_plan_edit.png` | 계획 수정 · 삭제 반영 |
| `4-5_swagger.png` | FastAPI `/docs` |
| `4-5_auth_banner.png` | 키 누락 경고 배너 |
| `4-6_calendar_local.png` · `4-6_calendar_google.png` | 캘린더 등록 (로컬 · Google) |
| `4-6_calendar_conflict.png` | 기존 일정으로 창이 줄어든 판정 |

---

## 12. 셀프 체크리스트 (미제공)

3일차 오후 제공 예정. 받으면 항목 · 결과 · 근거를 이 절에 표로 옮긴다.

| 항목 | 결과 | 근거 |
|---|---|---|
| (미제공) | | |
