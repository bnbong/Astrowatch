# Astrowatch 설치 · 설정 안내

> 실행 환경, API 키 발급, 환경변수. 갱신일 2026-11-18.

## 1. 실행 (Windows)

```bat
:: 1) 가상환경 · 의존성
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

:: 2) 환경변수 — 값을 채운다 (2절)
copy .env.example .env

:: 3) 서버
cd backend
..\.venv\Scripts\python -m uvicorn astrowatch.app.main:app --reload --port 8000
```

| 주소 | 내용 |
|---|---|
| http://127.0.0.1:8000 | 대화 화면 (`/plans.html` 계획, `/logs.html` 실행 로그) |
| http://127.0.0.1:8000/docs | FastAPI Swagger |

macOS: `source .venv/bin/activate`, `cp .env.example .env`. `.env` 와 `secrets/` 는 기기 간 수동 복사.

**테스트** — 키 · 네트워크 없이 `samples/` fixture 로 실행

```bat
cd backend
..\.venv\Scripts\python -X utf8 -m pytest
```

> 키가 하나도 없어도 서버는 뜬다. LLM 키가 없으면 사용법 안내만, 외부 키가 없으면 해당 Tool 이 `AUTH` 로 실패하고 나머지는 진행한다.
> 재계획 · 상한을 재현하려면 `.env` 에 `WEATHER_PROVIDER=mock` (모든 시간 흐림 고정).

## 2. API 키 발급

| 키 | 발급 절차 | `.env` |
|---|---|---|
| 공공데이터포털 | ① [data.go.kr](https://www.data.go.kr) 회원가입 ② **활용신청 5건**: 천문연 천문현상(15012691) · 출몰시각(15012688) · 월령(15012689), 기상청 단기예보(15084084) · 중기예보(`MidFcstInfoService`) ③ 마이페이지 → **일반 인증키(Decoding)** 복사 | `DATA_GO_KR_KEY` |
| 카카오 | ① developers.kakao.com 앱 생성 ② **REST API 키** 복사 ③ 앱 설정에서 **카카오맵(로컬) 사용 활성화** | `KAKAO_REST_KEY` |
| SSAFY GMS | GMS 에서 키 발급 (OpenAI 호환, base_url 기본값 사용) | `GMS_KEY` |
| OpenWeatherMap (선택) | openweathermap.org 무료 키 | `OPENWEATHER_KEY` |
| Google Calendar (심화) | ① Cloud Console 프로젝트 · **Calendar API 사용 설정** ② OAuth 동의 화면(테스트) + **테스트 사용자** 등록 ③ OAuth 클라이언트 **데스크톱 앱** JSON → `secrets/google_client_secret.json` ④ `cd backend && python scripts/google_auth.py` → 브라우저 로그인 → `secrets/google_token.json` ⑤ `CALENDAR_PROVIDER=google` | `GOOGLE_*`, `CALENDAR_PROVIDER` |

주의

- data.go.kr 키는 발급 후 동기화에 시간이 걸릴 수 있다(약 1시간).
- Encoding 키를 넣어도 `%` 가 있으면 코드가 Decoding 값으로 되돌린다(`backend/astrowatch/config.py`).
- `.env` · `secrets/` 는 커밋하지 않는다(`.gitignore`).

## 3. 환경변수 (`.env.example` 기준 · 값은 쓰지 않음)

| 변수 | 기본값 | 용도 |
|---|---|---|
| `GMS_KEY` | — | LLM 키 (analyze · respond) |
| `OPENAI_BASE_URL` | GMS 엔드포인트 | OpenAI 호환 base_url |
| `LLM_MODEL` | `gpt-6-luna` | 모델 |
| `LLM_REASONING_EFFORT` | `none` | gpt-6-luna 는 tools 사용 시 `none` 필수 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | F3022 용 (미사용) |
| `DATA_GO_KR_KEY` | — | 천문연 3종 · 기상청 2종 공통 |
| `KAKAO_REST_KEY` | — | 관측지 검색 |
| `WEATHER_PROVIDER` | `kma` | `kma` / `mock` |
| `OPENWEATHER_KEY` | — | 선택. 5일 예보 백업 |
| `MAX_REPLANS` | `3` | 재계획 상한 (NF3004) |
| `MIN_OBS_MINUTES` | `30` | 충족 최소 관측 시간 |
| `TIMEZONE` | `Asia/Seoul` | |
| `DB_PATH` | `data/astrowatch.db` | SQLite |
| `CHROMA_DIR` | `data/chroma` | F3022 용 (미사용) |
| `CALENDAR_PROVIDER` | `local` | `local` / `google` |
| `GOOGLE_CLIENT_SECRET_FILE` | `secrets/google_client_secret.json` | |
| `GOOGLE_TOKEN_FILE` | `secrets/google_token.json` | |
| `GOOGLE_CALENDAR_ID` | `primary` | |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | |

## 4. 저장소 구조

```
backend/astrowatch/   agent · tools · clients · events · logging_agent · storage · astro · data · app
backend/tests/        pytest (키 없이 실행)
backend/scripts/      check_apis.py (API 점검) · google_auth.py (토큰) · build_sky_objects.py (카탈로그)
frontend/             index.html · plans.html · logs.html · css · js
samples/              외부 API 원본 응답 · 실행 결과 샘플 (키 제거)
docs/                 기획안 · 요구사항 · 설계 · Tool · API · 검증 · 로그 샘플 · 이 문서
output/e2e-review/    Codex 실제 E2E 리뷰 기록
```
