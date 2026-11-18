# Astrowatch 실행 로그 샘플

> 대표 요청 R1 의 전체 실행 단계와, 재계획이 일어난 R2(mock) 구간을 `ChatResponse.steps` 그대로 옮긴다.
> 갱신일 2026-11-18 · 원본: `samples/run_R1.json`, `samples/run_R2_mock.json`

| 읽는 법 | |
|---|---|
| seq | 세션 안 발생 순서. seq 1 `session_start` · 마지막 `session_end` 는 `run_logs` 에만 있고 `steps` 에는 없다 |
| 시각 표기 | `02:00+1` = 다음 날 02:00 |
| 캡처 시점 | E2E 리뷰 수정 **이전** 코드 (judge 종료 문구가 지금은 `stop_reason` 별로 다르다) |
| 전체 이벤트 | 실행 중이면 `GET /api/logs/{session_id}` (payload 포함) |

---

## 1. R1 — 한 번에 충족 (실측 · 실제 키)

| 항목 | 값 |
|---|---|
| 요청 | 2026-12-14 서울 22:00~02:00 쌍둥이자리 유성우 (원문은 샘플에 미저장 — 기획안 R1) |
| session_id | `cd0f21493828` |
| 결과 | `plan` · **조건부 충족** 1건 · 재계획 0 · 경고 0 |
| 판정 근거 | 어두운 18:48~06:05+1 ∩ 내 22:00~02:00+1 → 창 240분 · 월령 5.5 · 예보 범위 밖 |

| seq | type | stage | tool | ok | ms | summary |
|---|---|---|---|---|---|---|
| 2 | stage_start | analyze | | | | 요청 분석 시작 |
| 3 | llm_decision | analyze | | | | 의도 판단: plan - 서울에서 특정 날짜와 시간대에 쌍둥이자리 유성우를 관측할 수 있는지 묻고 있습니다. |
| 4 | stage_end | analyze | | | 3407 | 의도 plan |
| 5 | stage_start | plan | | | | 작업 계획 시작 |
| 6 | stage_end | plan | | | 0 | 3단계 계획: 현상 → 출몰·월령 → 날씨 (search_sky_events → get_night_window → get_sky_forecast) |
| 7 | stage_start | execute | | | | Tool 실행 시작 |
| 8 | tool_call | execute | search_sky_events | | | 천문현상 조회 호출 (year=2026, month=12, kind=meteor) |
| 9 | tool_result | execute | search_sky_events | ✅ | 180 | 천문현상 조회 성공 |
| 10 | tool_call | execute | get_night_window | | | 박명·출몰 조회 호출 (date=2026-12-14) |
| 11 | tool_result | execute | get_night_window | ✅ | 826 | 박명·출몰 조회 성공 |
| 12 | tool_call | execute | get_sky_forecast | | | 날씨 예보 조회 호출 (date=2026-12-14, hours=22~25시) |
| 13 | tool_result | execute | get_sky_forecast | ❌ | 0 | 날씨 예보 조회 실패(OUT_OF_RANGE): 예보 범위 밖: 가장 늦은 예보일 2026-11-28 |
| 14 | stage_end | execute | | | 1007 | Tool 호출 3회(실패 1) · 캐시 재사용 0회 · 후보 1개 |
| 15 | stage_start | judge | | | | 판정 시작 |
| 16 | stage_end | judge | | | 0 | 조건부 충족 1 |
| 17 | stage_start | respond | | | | 응답 작성 시작 |
| 18 | stage_end | respond | | | 3382 | plan 응답 485자 |

```mermaid
flowchart LR
  A["analyze 3.4s<br/>LLM: plan"] --> P["plan<br/>3단계"]
  P --> E1["search_sky_events ✅ 180ms<br/>12/14 23:00 극대"]
  E1 -->|date| E2["get_night_window ✅ 826ms<br/>18:48~06:05+1 · 월령 5.5"]
  E2 -->|hours 22~25| E3["get_sky_forecast ❌<br/>OUT_OF_RANGE"]
  E3 --> J["judge<br/>조건부 충족 240분"]
  J --> R["respond 3.4s<br/>늦어도 01:30+1 도착"]
```

읽을 점

- 예보 Tool 실패(`OUT_OF_RANGE`)에도 흐름은 진행했고, 판정은 **조건부 충족**으로 떨어졌다 (F3007 · F3008).
- 현상 Tool 의 `date` 가 박명 Tool 입력이 되고, 박명 결과의 어두운 창이 예보 `hours` 가 됐다.
- LLM 은 analyze · respond 두 번만. 판정은 0ms (코드).

---

## 2. R2 — 재계획 발생 구간 (mock)

> ⚠️ **mock**: 날씨만 `WEATHER_PROVIDER=mock` (모든 시간 흐림 · 강수확률 80). LLM · 천문연은 실제 호출.
> 그래서 재계획해도 충족이 나올 수 없고 상한(3회)까지 간다.

| 항목 | 값 |
|---|---|
| 조건 | 2026-11-21~22 · 대전 · 21:00~00:00+1 · 사자자리 유성우 · 고정 없음 |
| 결과 | 재계획 3회 (날짜 ±2일 → 다른 현상 → 세종) · 추천 0 · 제외 8 · steps 84개 |

**첫 판정 → 재계획 1회차 → 재계획 2회차 진입 (seq 7~31)**

| seq | type | stage | tool | ok | ms | 회차 | summary |
|---|---|---|---|---|---|---|---|
| 7 | stage_start | execute | | | | | Tool 실행 시작 |
| 8 | tool_call | execute | search_sky_events | | | | 천문현상 조회 호출 (year=2026, month=11, kind=meteor) |
| 9 | tool_result | execute | search_sky_events | ✅ | 207 | | 천문현상 조회 성공 |
| 10 | stage_end | execute | | | 208 | | Tool 호출 1회(실패 0) · 캐시 재사용 0회 · 후보 0개 |
| 11 | stage_start | judge | | | | | 판정 시작 |
| **12** | **replan** | judge | | | | **1** | **재계획 1회차: 충족·조건부 충족 후보 0개 → 같은 현상 다른 날짜(±2일): 날짜 범위를 2026-11-19~2026-11-24 로 넓힘** |
| 13 | stage_end | judge | | | 0 | 1 | 후보 0개 · 재계획 1회차(같은 현상 다른 날짜(±2일)) |
| 14 | stage_start | plan | | | | 1 | 작업 계획 시작 |
| 15 | stage_end | plan | | | 0 | 1 | [재계획 1회차] 3단계 계획: 현상 → 출몰·월령 → 날씨 |
| 16 | stage_start | execute | | | | 1 | Tool 실행 시작 |
| 17 | tool_call | execute | get_night_window | | | 1 | 박명·출몰 조회 호출 (date=2026-11-19) |
| 18 | tool_result | execute | get_night_window | ✅ | 872 | 1 | 박명·출몰 조회 성공 |
| 19 | tool_call | execute | get_night_window | | | 1 | 박명·출몰 조회 호출 (date=2026-11-20) |
| 20 | tool_result | execute | get_night_window | ✅ | 779 | 1 | 박명·출몰 조회 성공 |
| 21 | tool_call | execute | get_sky_forecast | | | 1 | 날씨 예보 조회 호출 (date=2026-11-19, hours=21~23시) |
| 22 | tool_result | execute | get_sky_forecast | ✅ | 0 | 1 | 날씨 예보 조회 성공 - mock 공급자 — 실제 예보가 아니다 |
| 23 | tool_call | execute | get_sky_forecast | | | 1 | 날씨 예보 조회 호출 (date=2026-11-20, hours=21~23시) |
| 24 | tool_result | execute | get_sky_forecast | ✅ | 0 | 1 | 날씨 예보 조회 성공 - mock 공급자 — 실제 예보가 아니다 |
| 25 | stage_end | execute | | | 1652 | 1 | Tool 호출 4회(실패 0) · **캐시 재사용 1회** · 후보 2개 |
| 26 | stage_start | judge | | | | 1 | 판정 시작 |
| **27** | **replan** | judge | | | | **2** | **재계획 2회차: 충족·조건부 충족 후보 0개 (관측 창 0분이 최소 30분보다 짧음) → 같은 기간 다른 현상: '사자자리 유성우' 지정을 풀고 같은 기간의 다른 현상 탐색** |
| 28 | stage_end | judge | | | 0 | 2 | 불충족 2 · 재계획 2회차(같은 기간 다른 현상) |
| 29 | stage_start | plan | | | | 2 | 작업 계획 시작 |
| 30 | stage_end | plan | | | 0 | 2 | [재계획 2회차] 3단계 계획: 현상 → 출몰·월령 → 날씨 |
| 31 | stage_start | execute | | | | 2 | Tool 실행 시작 |

**이후 요약**

| seq | 내용 |
|---|---|
| 50 | execute: Tool 호출 9회 · 캐시 재사용 4회 · 후보 8개 |
| 52 | replan 3회차: 인접 지역 — 대전 → 세종 |
| 81 | execute: Tool 호출 12회 · 캐시 재사용 1회 · 후보 8개 |
| 83 | judge: 불충족 8 · 재계획 상한/대안 소진 (수정 전 문구 — 지금은 `stop_reason=limit_reached`) |
| 85 | respond: "날짜 범위나 지역을 더 넓혀 볼까요?" · "(검증용 모의 예보)" |

```mermaid
flowchart LR
  J0["judge 0<br/>후보 0개"] -->|"① 날짜 ±2일"| J1["judge 1<br/>불충족 2<br/>재사용 1"]
  J1 -->|"② 다른 현상"| J2["judge 2<br/>불충족 8<br/>재사용 4"]
  J2 -->|"③ 대전 → 세종"| J3["judge 3<br/>불충족 8"]
  J3 -->|상한 3| R[respond<br/>넓힐지 질문]
```

읽을 점

- 회차 1에서 11월 현상 조회 결과를 다시 부르지 않았다 (seq 25 "캐시 재사용 1회", F3009).
- 재계획 이후 단계에는 `replan_round` 가 붙어 화면에서 구분된다 (F3019).
- 현상 · 날짜 조건이 고정되지 않아 ① → ② → ③ 이 모두 적용됐다.
