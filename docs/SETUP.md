# Astrowatch 설치, 설정 안내

> 실행 환경, API 키 발급, 환경변수, RAG 색인. 갱신일 2026-11-20 (오후 심화 기능 반영).

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
| http://127.0.0.1:8000 | 대화 화면 (`/plans.html` 계획, `/logs.html` 실행 로그, `/pointer.html` 하늘 가리키기) |
| http://127.0.0.1:8000/docs | FastAPI Swagger |

macOS: `source .venv/bin/activate`, `cp .env.example .env`. `.env` 와 `secrets/` 는 기기 간 수동 복사.

**테스트**: 키, 네트워크 없이 `samples/` fixture 로 실행

```bat
cd backend
..\.venv\Scripts\python -X utf8 -m pytest
```

> 키가 하나도 없어도 서버는 뜬다. LLM 키가 없으면 사용법 안내만, 외부 키가 없으면 해당 Tool 이 `AUTH` 로 실패하고 나머지는 진행한다.
> 재계획, 상한을 재현하려면 `.env` 에 `WEATHER_PROVIDER=mock` (모든 시간 흐림 고정).

**배경 지식 색인 (F3022, 선택)**: 색인이 없어도 서버는 뜨고, 배경 지식 질문에만 "색인 없음"(`NO_INDEX`)으로 답한다.

```bat
:: 1) 문서 수집 (한국어 위키백과, 키 불필요). 결과 data/space_docs.json 은 저장소에 커밋돼 있어 보통 생략
.venv\Scripts\python -X utf8 backend\scripts\collect_space_docs.py
:: 2) 색인 만들기: 청크 → 임베딩 → data/chroma/space_docs
.venv\Scripts\python -X utf8 backend\scripts\build_space_docs.py
```

| 항목 | 내용 |
|---|---|
| 순서 | collect → build. 수집은 1초 간격, 429 면 기다렸다 재시도, 받은 본문은 `data/cache` 에 남겨 이어받기 |
| 임베딩 | 기본 `EMBEDDING_PROVIDER=local`: chromadb 내장 ONNX `all-MiniLM-L6-v2` (384차원, 키 불필요). **첫 build 때 모델을 한 번 내려받는다** |
| 결과 | 문서 321건 → 청크 724개 (2026-11-20) |
| 커밋 여부 | `data/space_docs.json` 은 커밋, `data/chroma/`, `data/cache/` 는 `.gitignore` → 새 기기에서는 build 를 다시 실행 |
| 공급자 바꾸기 | `--provider gms` 로 만들면 `.env` 도 `EMBEDDING_PROVIDER=gms`. 다르면 Tool 이 `INDEX_MISMATCH` |

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
| `EMBEDDING_MODEL` | `text-embedding-3-small` | F3022, `EMBEDDING_PROVIDER=gms` 일 때만 |
| `DATA_GO_KR_KEY` | 없음 | 천문연 3종, 기상청 2종 공통 |
| `KAKAO_REST_KEY` | 없음 | 관측지 검색 |
| `WEATHER_PROVIDER` | `kma` | `kma` / `mock` |
| `OPENWEATHER_KEY` | 없음 | 선택. 5일 예보 백업 |
| `MAX_REPLANS` | `3` | 재계획 상한 (NF3004) |
| `MIN_OBS_MINUTES` | `30` | 충족 최소 관측 시간 |
| `TIMEZONE` | `Asia/Seoul` | |
| `DB_PATH` | `data/astrowatch.db` | SQLite |
| `CHROMA_DIR` | `data/chroma` | F3022 색인 위치 (`space_docs` 컬렉션, gitignore) |
| `CALENDAR_PROVIDER` | `local` | `local` / `google` |
| `GOOGLE_CLIENT_SECRET_FILE` | `secrets/google_client_secret.json` | |
| `GOOGLE_TOKEN_FILE` | `secrets/google_token.json` | |
| `GOOGLE_CALENDAR_ID` | `primary` | |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | |
| `APP_ALLOWED_ORIGINS` | 없음 | 추가 허용 출처 (쉼표). 터널 데모용 |
| `APP_PUBLIC_URL` | 없음 | 배포 주소(예: `https://astrowatch.onrender.com`). 허용 출처와 Host 에 자동 포함. Render 가 주는 `RENDER_EXTERNAL_URL` 도 같은 역할 |
| `APP_ALLOWED_HOSTS` | 없음 | 추가 허용 Host (쉼표, 포트 제외) |
| `APP_ACCESS_TOKEN` | 없음 | 설정 시 `/api/*` 에 `X-Astrowatch-Token` 필요 |
| `CHAT_RATE_PER_MIN` | `12` | `/api/chat` IP 당 분당 요청 수 |
| `AUDIT_RATE_PER_MIN` | `6` | `POST /api/logs/{id}/audit` (지금 판정, LLM 진단) IP 당 분당 요청 수 |
| `ALLOW_EXTERNAL_PATHS` | `0` | `1` 이면 저장소 밖 절대 경로 허용 |

심화 기능 (`.env.example` 에 주석으로 있음, 비우면 기본값)

| 변수 | 기본값 | 용도 |
|---|---|---|
| `AGENT_STRUCTURE` | `planner_worker` | F3021. `planner_worker`(기본) / `classic`(구조 변경 전 비교용) |
| `WORKER_LLM` | `1` | F3021. 인자를 고칠 수 있는 실패(NO_RESULT 등)에만 Worker LLM 판단. `0` 이면 규칙만 |
| `WORKER_MAX_RETRY` | `2` | F3021. 같은 Tool 재시도 상한 |
| `AUDIT_LLM` | `1` | F3020. 이상(anomaly) 판정에만 LLM 원인 진단. `0` 이면 진단 없음 |
| `AUDIT_REPLAN_THRESHOLD` | `3` | F3020. 한 턴 재계획이 이 횟수 이상이면 과다 재계획 |
| `EMBEDDING_PROVIDER` | `local` | F3022. `local`(ONNX all-MiniLM-L6-v2, 키 불필요) / `gms`(OpenAI 호환 임베딩 API) |
| `SPACE_DOCS_FILE` | `data/space_docs.json` | F3022. 수집 문서 파일 |

### 보안 설정

기본값(127.0.0.1 바인딩, 토큰 없음)은 혼자 쓰는 로컬 실행용이다. LAN, 터널로 열 때는 아래를 함께 설정한다.

- **허용 출처 (CORS, CSRF)**: `http://127.0.0.1:{APP_PORT}`, `http://localhost:{APP_PORT}`, `http://{APP_HOST}:{APP_PORT}`(APP_HOST 가 `0.0.0.0` 이 아닐 때) 와, Origin 의 host:port 가 요청 `Host` 와 같은 같은 출처(포트를 바꿔 띄워도 화면 요청이 막히지 않게)만 허용한다. 다른 출처에서 온 `POST/PATCH/DELETE` 는 403, 브라우저가 `Sec-Fetch-Site: cross-site` 를 붙인 상태 변경 요청도 403 이다. 모든 상태 변경 요청은 `X-Requested-With: Astrowatch` 헤더가 있어야 하고(프론트 `api.js` 가 항상 붙임), 본문은 `application/json` 만(그 외 415), 256KB 이하(초과 413)만 받는다.
- **터널 데모 (`APP_ALLOWED_ORIGINS`)**: cloudflared, ngrok 처럼 다른 주소로 접속하면 그 주소를 출처로 추가해야 저장, 삭제가 된다.
  예: `APP_ALLOWED_ORIGINS=https://xxxx.trycloudflare.com` (끝의 `/` 는 무시). 그 호스트는 Host 허용 목록에도 자동으로 들어간다.
  LAN 에서 `APP_HOST=0.0.0.0` 으로 열면 접속할 IP 를 `APP_ALLOWED_HOSTS=192.168.0.10` 과 `APP_ALLOWED_ORIGINS=http://192.168.0.10:8000` 에 적는다. 목록에 없는 Host 헤더는 400 이다.
- **접근 토큰 (`APP_ACCESS_TOKEN`)**: 터널, LAN 데모 때는 길고 무작위인 값을 설정한다(예: `python -c "import secrets;print(secrets.token_urlsafe(24))"`). 브라우저는 처음 401 을 받으면 입력 폼을 띄우고 그 탭의 `sessionStorage` 에만 보관한다. `/api/health` 와 정적 화면은 토큰 없이 열린다.
- **토큰 파일 권한**: `scripts/google_auth.py` 와 토큰 자동 갱신은 `secure_files.write_private` 로 저장한다: 같은 폴더의 임시 파일(0600)에 쓰고 원자 교체, 부모 폴더가 없으면 0700 으로 만든다. 토큰 파일이 심볼릭 링크면 읽기, 쓰기를 모두 거부하고, POSIX 에서 그룹/타인 권한이 있으면 경고 후 0600 으로 고친다(Windows 는 권한 조정 생략). `GOOGLE_*` 경로가 `frontend/`(정적 서빙) 아래이거나 일반 파일이 아니면(디렉터리, 링크, reparse point) 설정에서 거부된다.
- **경로 (`ALLOW_EXTERNAL_PATHS`)**: `DB_PATH`, `CHROMA_DIR`, `GOOGLE_*` 의 상대 경로는 저장소 루트 기준이며 루트 밖으로 나가면 거부된다. `..`, 널 문자가 섞인 값도 거부한다. 절대 경로는 `ALLOW_EXTERNAL_PATHS=1` 일 때만 쓰며, 아니면 경고 로그 후 해당 기능이 꺼진다(DB 는 기본 경로로 돌아감). `CHROMA_DIR`, `SPACE_DOCS_FILE` 은 수집기와 색인이 파일을 쓰므로 `data/` 아래(또는 `ALLOW_EXTERNAL_PATHS=1` 일 때 저장소 밖 절대 경로)만 허용하고, 아니면 경고 후 기본값을 쓴다.
- **로그**: 설정된 키 값과 토큰 파일의 `token`, `refresh_token`, `client_secret` 은 로그에서 `***` 로 바뀐다(`astrowatch/redact.py`). 외부 API 예외는 URL 이 담긴 원본 예외 체인을 끊고 다시 던진다.
- **응답 헤더**: 모든 응답에 `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, API 응답엔 `Cache-Control: no-store`, HTML 엔 CSP(`script-src 'self'`) 가 붙는다. `/docs`, `/redoc` 만 Swagger CDN 을 위해 완화된 CSP 를 쓴다.

### 휴대폰에서 하늘 가리키기 테스트

`pointer.html`(계획 목록의 "하늘에서 찾기")은 휴대폰 방향 센서(DeviceOrientation)와 현재 위치(Geolocation)를 쓴다.
두 기능은 브라우저가 **보안 컨텍스트**(HTTPS 또는 `localhost`)에서만 열어 준다. `http://192.168.x.x:8000` 처럼 LAN IP 로 열면 센서가 "미지원"으로 나온다.

| 방법 | 준비 | 휴대폰에서 여는 주소 | 센서 | 현재 위치 |
|---|---|---|---|---|
| (a) Android Chrome USB 포트 포워딩 | USB 디버깅 켜고 PC 와 USB 연결 | `http://localhost:8000/plans.html` | ✅ | ✅ |
| (b) cloudflared, ngrok 터널 | 터널 주소를 허용 출처에, 접근 토큰 설정 | `https://xxxx.trycloudflare.com/plans.html` | ✅ (iOS 포함) | ✅ |
| (c) 데스크톱 시뮬레이션 | 없음 | PC 브라우저 `http://127.0.0.1:8000/pointer.html?key=안드로메다 은하` | 마우스 끌기, 방향키 | 계획 좌표 |

**(a) Android Chrome USB 포트 포워딩** (같은 Wi-Fi 필요 없음)

1. 휴대폰: 설정 → 개발자 옵션 → USB 디버깅 켜기, USB 로 PC 와 연결해 디버깅 허용.
2. PC Chrome 에서 `chrome://inspect/#devices` → **Port forwarding...** → `8000` → `localhost:8000` 추가, "Enable port forwarding" 체크.
3. 서버는 평소처럼 `127.0.0.1:8000` 으로 띄운다.
4. 휴대폰 Chrome 에서 `http://localhost:8000` 을 연다. 휴대폰 입장에서 localhost 이므로 보안 컨텍스트로 인정된다.

**(b) 터널** (iOS Safari 는 이 방법만 가능)

```powershell
cloudflared tunnel --url http://127.0.0.1:8000      # 또는: ngrok http 8000
```

- 출력된 `https://...` 주소를 `.env` 의 `APP_ALLOWED_ORIGINS` 에 넣고 서버를 다시 띄운다 (위 "터널 데모" 참고).
- 외부에 열리므로 `APP_ACCESS_TOKEN` 을 꼭 설정한다. 휴대폰에서 처음 열 때 토큰 입력 창이 뜬다.
- iOS 13 이상은 "센서 시작"을 누르면 "동작 및 방향 접근" 허용 창이 뜬다. 거부했다면 Safari 설정에서 다시 허용해야 한다.

**(c) 데스크톱 시뮬레이션**

- 센서가 없는 PC 에서는 시뮬레이션 모드가 자동으로 켜진다. 조준경을 마우스로 끌거나 방향키(Shift 는 10°)로 돌린다.
- 주소에 `az`, `alt`(처음 가리키는 방향), `at`(시각 고정)을 붙일 수 있다. 예: `pointer.html?key=안드로메다 은하&at=2026-11-20T21:00:00%2B09:00&az=60&alt=55`
- 화면 캡처는 `scripts/capture_screens.py --only pointer` (`docs/screenshots/22_pointer_desktop_sim.png`, `23_pointer_mobile.png`).

| 화면 상태 | 뜻 |
|---|---|
| 센서 대기 | 아직 "센서 시작"을 누르지 않음 |
| 센서 켜짐 (방위 기준 없음) | 북쪽 기준이 없는 기기. 위아래 안내만 믿을 수 있다 |
| 센서 미지원 | 1.5초 안에 센서 값이 없음, 또는 보안 컨텍스트가 아님 → 시뮬레이션으로 전환 |
| 센서 권한 거부 | iOS 에서 허용하지 않음 |
| 위치 계획 좌표, 계획 지역, 기본값 | "현재 위치 사용"을 누르기 전 계산 기준 (기본값은 서울) |

나침반은 자북 기준이라 한국 주변은 자기 편각(서편 약 9°)을 자동으로 더한다. 철제 난간, 자동차 근처에서는 방위가 수십 도 어긋날 수 있다.

## 4. 저장소 구조

```
backend/astrowatch/   agent, tools, clients, events, logging_agent, storage, astro, data, app
backend/tests/        pytest (키 없이 실행)
backend/scripts/      check_apis.py (API 점검), google_auth.py (토큰), build_sky_objects.py (카탈로그),
                      run_scenarios.py (실측 샘플), capture_screens.py (화면), make_anomalies.py (F3020),
                      compare_structures.py (F3021), collect_space_docs.py, build_space_docs.py (F3022)
backend/astrowatch/rag/  F3022 수집, 청크, 색인, 검색
frontend/             index.html, plans.html, logs.html, pointer.html (+ js/pointer.js, js/skymath.js), css, js
data/                 space_docs.json (커밋), astrowatch.db, chroma/, cache/ (gitignore)
samples/              외부 API 원본 응답, 실행 결과 샘플 (키 제거)
docs/                 기획안, 요구사항, 설계, Tool, API, 검증, 로그 샘플, 이 문서
output/e2e-review/    Codex 실제 E2E 리뷰 기록
```

## 5. Render 배포

저장소 루트의 [render.yaml](../render.yaml)(Blueprint)이 무료 웹 서비스 하나를 정의한다. 휴대폰 센서 테스트처럼 HTTPS 주소가 필요할 때 쓴다.

| 단계 | 할 일 |
|---|---|
| 1 | GitHub 저장소(`github` 리모트, `master` 브랜치)에 푸시한다. `data/space_docs.json` 이 함께 올라가야 빌드 때 색인을 만든다 |
| 2 | Render 대시보드에서 New > Blueprint > 이 저장소 선택. render.yaml 대로 `astrowatch` 서비스가 만들어진다 |
| 3 | Environment 에서 `sync: false` 로 둔 키를 넣는다: `DATA_GO_KR_KEY`, `KAKAO_REST_KEY`, 필요하면 `GMS_KEY`, `OPENWEATHER_KEY`. 없는 키의 기능은 안내 문구로 대체된다 |
| 4 | `APP_ACCESS_TOKEN` 은 자동 생성된다. 화면을 처음 열면 토큰 입력 폼이 뜨므로 Environment 에서 값을 복사해 넣는다 |
| 5 | 배포가 끝나면 `https://<서비스 이름>.onrender.com` 으로 접속한다. 휴대폰에서는 같은 주소의 `/plans.html` 에서 "하늘에서 찾기" 를 누른다 |

동작 방식과 제한

- **빌드**: `pip install` 뒤 `backend/scripts/build_space_docs.py` 가 커밋된 문서 JSON 으로 Chroma 색인을 만든다. 임베딩 모델(약 80MB)은 이때 한 번 내려받는다. 빌드 산출물은 런타임에도 그대로 쓰인다.
- **실행**: `uvicorn --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"`. 프록시 뒤라서 X-Forwarded-For 를 믿어야 `/api/chat` 속도 제한이 실제 사용자 IP 기준이 된다.
- **출처와 Host**: Render 가 주는 `RENDER_EXTERNAL_URL` 을 설정이 자동으로 허용 출처와 Host 목록에 넣는다. 커스텀 도메인은 `APP_PUBLIC_URL` 에 적는다. 같은 호스트에서 온 요청은 CSRF 검사도 통과한다.
- **저장소**: SQLite(계획, 실행 로그, 판정, 세션)는 서비스 파일 시스템에 쓰인다. 무료 플랜은 영구 디스크가 없어 재배포나 재시작 때 초기화된다. 데모용으로만 쓴다.
- **무료 플랜**: 15분 동안 요청이 없으면 잠들고 첫 요청에 30초 넘게 걸린다. 워크스페이스당 월 750 인스턴스 시간을 모든 무료 서비스가 나눠 쓴다. 메모리 512MB 에서 chromadb 와 onnxruntime 이 빠듯하면 `search_space_docs` 가 NO_INDEX 로 답하도록 색인 빌드를 빼면 된다(buildCommand 에서 두 번째 명령 삭제).
- **Google Calendar**: 토큰 파일을 배포본에 두지 않으므로 `CALENDAR_PROVIDER=local` 로 고정한다.
- **LLM**: GMS 키는 2026-11-20 까지만 유효하다. 그 뒤에는 키를 비워 두면 사용법 안내와 템플릿 답변으로 동작한다.

