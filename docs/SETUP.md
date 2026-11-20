# Astrowatch 설치, 설정 안내

> 실행 환경, API 키 발급, 환경변수. 갱신일 2026-11-20.

## 1. 실행 (Windows)

```bat
:: 1) 가상환경, 의존성
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

:: 2) 환경변수: 값을 채운다 (2절)
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

**테스트**: 키, 네트워크 없이 `samples/` fixture 로 실행

```bat
cd backend
..\.venv\Scripts\python -X utf8 -m pytest
```

> 키가 하나도 없어도 서버는 뜬다. LLM 키가 없으면 사용법 안내만, 외부 키가 없으면 해당 Tool 이 `AUTH` 로 실패하고 나머지는 진행한다.
> 재계획, 상한을 재현하려면 `.env` 에 `WEATHER_PROVIDER=mock` (모든 시간 흐림 고정).

## 2. API 키 발급

| 키 | 발급 절차 | `.env` |
|---|---|---|
| 공공데이터포털 | ① [data.go.kr](https://www.data.go.kr) 회원가입 ② **활용신청 5건**: 천문연 천문현상(15012691), 출몰시각(15012688), 월령(15012689), 기상청 단기예보(15084084), 중기예보(`MidFcstInfoService`) ③ 마이페이지 → **일반 인증키(Decoding)** 복사 | `DATA_GO_KR_KEY` |
| 카카오 | ① developers.kakao.com 앱 생성 ② **REST API 키** 복사 ③ 앱 설정에서 **카카오맵(로컬) 사용 활성화** | `KAKAO_REST_KEY` |
| SSAFY GMS | GMS 에서 키 발급. 기본은 Anthropic 호환 경로(`claude-haiku-5-5`), `LLM_PROVIDER=openai` 로 바꾸면 OpenAI 경로(`gpt-6-luna`) | `GMS_KEY` (또는 `GMS_API_KEY`) |
| OpenWeatherMap (선택) | openweathermap.org 무료 키 | `OPENWEATHER_KEY` |
| Google Calendar (심화) | ① Cloud Console 프로젝트, **Calendar API 사용 설정** ② OAuth 동의 화면(테스트) + **테스트 사용자** 등록 ③ OAuth 클라이언트 **데스크톱 앱** JSON → `secrets/google_client_secret.json` ④ `cd backend && python scripts/google_auth.py` → 브라우저 로그인 → `secrets/google_token.json` ⑤ `CALENDAR_PROVIDER=google` | `GOOGLE_*`, `CALENDAR_PROVIDER` |

주의

- data.go.kr 키는 발급 후 동기화에 시간이 걸릴 수 있다(약 1시간).
- Encoding 키를 넣어도 `%` 가 있으면 코드가 Decoding 값으로 되돌린다(`backend/astrowatch/config.py`).
- `.env`, `secrets/` 는 커밋하지 않는다(`.gitignore`).

## 3. 환경변수 (`.env.example` 기준, 값은 쓰지 않음)

| 변수 | 기본값 | 용도 |
|---|---|---|
| `GMS_KEY` | 없음 | LLM 키 (analyze, respond). `GMS_API_KEY` 이름도 읽는다 |
| `OPENAI_BASE_URL` | 공급자 기본값 | 비우면 anthropic → `…/gmsapi/api.anthropic.com/v1`, openai → `…/gmsapi/api.openai.com/v1/`. 두 공급자가 같은 변수를 쓰므로 anthropic 으로 바꿀 때는 **비워 둔다** |
| `LLM_PROVIDER` | `anthropic` | `anthropic`(GMS Anthropic 호환, claude-haiku-5-5) / `openai`(GMS OpenAI 호환, gpt-6-luna) |
| `LLM_MODEL` | 공급자 기본값 | anthropic → `claude-haiku-5-5`, openai → `gpt-6-luna` |
| `LLM_REASONING_EFFORT` | `none` | openai 공급자 전용. gpt-6-luna 는 tools 사용 시 `none` 필수 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | F3022 용 (미사용) |
| `DATA_GO_KR_KEY` | 없음 | 천문연 3종, 기상청 2종 공통 |
| `KAKAO_REST_KEY` | 없음 | 관측지 검색 |
| `WEATHER_PROVIDER` | `kma` | `kma` / `mock` |
| `OPENWEATHER_KEY` | 없음 | 선택. 5일 예보 백업 |
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
| `APP_ALLOWED_ORIGINS` | 없음 | 추가 허용 출처 (쉼표). 터널 데모용 |
| `APP_ALLOWED_HOSTS` | 없음 | 추가 허용 Host (쉼표, 포트 제외) |
| `APP_ACCESS_TOKEN` | 없음 | 설정 시 `/api/*` 에 `X-Astrowatch-Token` 필요 |
| `CHAT_RATE_PER_MIN` | `12` | `/api/chat` IP 당 분당 요청 수 |
| `ALLOW_EXTERNAL_PATHS` | `0` | `1` 이면 저장소 밖 절대 경로 허용 |

### 보안 설정

기본값(127.0.0.1 바인딩, 토큰 없음)은 혼자 쓰는 로컬 실행용이다. LAN, 터널로 열 때는 아래를 함께 설정한다.

- **허용 출처 (CORS, CSRF)**: `http://127.0.0.1:{APP_PORT}`, `http://localhost:{APP_PORT}`, `http://{APP_HOST}:{APP_PORT}`(APP_HOST 가 `0.0.0.0` 이 아닐 때) 와, Origin 의 host:port 가 요청 `Host` 와 같은 같은 출처(포트를 바꿔 띄워도 화면 요청이 막히지 않게)만 허용한다. 다른 출처에서 온 `POST/PATCH/DELETE` 는 403, 브라우저가 `Sec-Fetch-Site: cross-site` 를 붙인 상태 변경 요청도 403 이다. 모든 상태 변경 요청은 `X-Requested-With: Astrowatch` 헤더가 있어야 하고(프론트 `api.js` 가 항상 붙임), 본문은 `application/json` 만(그 외 415), 256KB 이하(초과 413)만 받는다.
- **터널 데모 (`APP_ALLOWED_ORIGINS`)**: cloudflared, ngrok 처럼 다른 주소로 접속하면 그 주소를 출처로 추가해야 저장, 삭제가 된다.
  예: `APP_ALLOWED_ORIGINS=https://xxxx.trycloudflare.com` (끝의 `/` 는 무시). 그 호스트는 Host 허용 목록에도 자동으로 들어간다.
  LAN 에서 `APP_HOST=0.0.0.0` 으로 열면 접속할 IP 를 `APP_ALLOWED_HOSTS=192.168.0.10` 과 `APP_ALLOWED_ORIGINS=http://192.168.0.10:8000` 에 적는다. 목록에 없는 Host 헤더는 400 이다.
- **접근 토큰 (`APP_ACCESS_TOKEN`)**: 터널, LAN 데모 때는 길고 무작위인 값을 설정한다(예: `python -c "import secrets;print(secrets.token_urlsafe(24))"`). 브라우저는 처음 401 을 받으면 입력 폼을 띄우고 그 탭의 `sessionStorage` 에만 보관한다. `/api/health` 와 정적 화면은 토큰 없이 열린다.
- **토큰 파일 권한**: `scripts/google_auth.py` 와 토큰 자동 갱신은 `secure_files.write_private` 로 저장한다: 같은 폴더의 임시 파일(0600)에 쓰고 원자 교체, 부모 폴더가 없으면 0700 으로 만든다. 토큰 파일이 심볼릭 링크면 읽기, 쓰기를 모두 거부하고, POSIX 에서 그룹/타인 권한이 있으면 경고 후 0600 으로 고친다(Windows 는 권한 조정 생략). `GOOGLE_*` 경로가 `frontend/`(정적 서빙) 아래이거나 일반 파일이 아니면(디렉터리, 링크, reparse point) 설정에서 거부된다.
- **경로 (`ALLOW_EXTERNAL_PATHS`)**: `DB_PATH`, `CHROMA_DIR`, `GOOGLE_*` 의 상대 경로는 저장소 루트 기준이며 루트 밖으로 나가면 거부된다. `..`, 널 문자가 섞인 값도 거부한다. 절대 경로는 `ALLOW_EXTERNAL_PATHS=1` 일 때만 쓰며, 아니면 경고 로그 후 해당 기능이 꺼진다(DB 는 기본 경로로 돌아감).
- **로그**: 설정된 키 값과 토큰 파일의 `token`, `refresh_token`, `client_secret` 은 로그에서 `***` 로 바뀐다(`astrowatch/redact.py`). 외부 API 예외는 URL 이 담긴 원본 예외 체인을 끊고 다시 던진다.
- **응답 헤더**: 모든 응답에 `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, API 응답엔 `Cache-Control: no-store`, HTML 엔 CSP(`script-src 'self'`) 가 붙는다. `/docs`, `/redoc` 만 Swagger CDN 을 위해 완화된 CSP 를 쓴다.

## 4. 저장소 구조

```
backend/astrowatch/   agent, tools, clients, events, logging_agent, storage, astro, data, app
backend/tests/        pytest (키 없이 실행)
backend/scripts/      check_apis.py (API 점검), google_auth.py (토큰), build_sky_objects.py (카탈로그)
frontend/             index.html, plans.html, logs.html, css, js
samples/              외부 API 원본 응답, 실행 결과 샘플 (키 제거)
docs/                 기획안, 요구사항, 설계, Tool, API, 검증, 로그 샘플, 이 문서
output/e2e-review/    Codex 실제 E2E 리뷰 기록
```
