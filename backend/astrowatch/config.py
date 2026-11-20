"""실행 설정 — .env 에서 읽는다. 키는 소스에 적지 않는다 (NF3001).

로드 순서: 저장소 루트의 .env → OS 환경변수. 둘 다 없으면 None/기본값.
키가 없어도 import 는 실패하지 않는다. 키가 필요한 Tool 이 AUTH 실패를 돌려준다 (NF3003).
"""

from __future__ import annotations

import logging
import os
import stat
import urllib.parse
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# backend/astrowatch/config.py → 저장소 루트
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env", override=False)

logger = logging.getLogger("astrowatch.config")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _data_go_kr_key() -> str | None:
    """공공데이터포털 키. Encoding 키(%2B 등 포함)를 넣어도 Decoding 값으로 되돌린다.

    requests 가 파라미터를 다시 인코딩하므로 Decoding 값이어야 한 번만 인코딩된다.
    """
    raw = _env("DATA_GO_KR_KEY")
    if raw and "%" in raw:
        return urllib.parse.unquote(raw)
    return raw


def _env_flag(name: str) -> bool:
    return (_env(name) or "").lower() in ("1", "true", "yes", "on")


def _resolve(path: str | None, *, name: str = "path") -> Path | None:
    """설정 경로를 안전하게 해석한다 (경로 조작 방어).

    - 널 문자 · ``..`` 세그먼트가 섞인 값은 거부한다.
    - 상대 경로는 ROOT_DIR 기준으로 resolve() 하고, 결과가 ROOT_DIR 안이어야 한다.
    - 절대 경로는 ALLOW_EXTERNAL_PATHS=1 일 때만 허용한다.
    거부하면 경고 로그(값은 남기지 않음) 후 None — 해당 기능은 비활성화된다.
    """
    if not path:
        return None
    if chr(0) in path:
        logger.warning("%s: 널 문자가 포함된 경로를 거부함", name)
        return None
    parts = path.replace("\\", "/").split("/")
    if ".." in parts:
        logger.warning("%s: '..' 이 포함된 경로를 거부함", name)
        return None
    p = Path(path)
    if p.is_absolute() or p.drive or p.root:
        if not _env_flag("ALLOW_EXTERNAL_PATHS"):
            logger.warning("%s: 저장소 밖 절대 경로는 ALLOW_EXTERNAL_PATHS=1 일 때만 허용됨", name)
            return None
        return p.resolve()
    resolved = (ROOT_DIR / p).resolve()
    if not resolved.is_relative_to(ROOT_DIR):  # 심볼릭 링크로 밖을 가리키는 경우
        logger.warning("%s: 저장소 밖을 가리키는 경로를 거부함", name)
        return None
    return resolved


FRONTEND_DIR = ROOT_DIR / "frontend"
_REPARSE_POINT = 0x400  # stat.FILE_ATTRIBUTE_REPARSE_POINT (Windows)


def _resolve_secret(path: str | None, *, name: str) -> Path | None:
    """비밀 파일(GOOGLE_*) 경로. _resolve 규칙 + 정적 서빙 폴더(frontend/) 아래 금지 + 일반 파일만.

    파일이 아직 없으면(토큰 발급 전) 경로는 허용한다. 있으면 심볼릭 링크 · 디렉터리 · reparse point 를 거부한다.
    """
    resolved = _resolve(path, name=name)
    if resolved is None:
        return None
    if resolved == FRONTEND_DIR or resolved.is_relative_to(FRONTEND_DIR):
        logger.warning("%s: 정적 서빙 폴더(frontend/) 아래의 비밀 파일 경로를 거부함", name)
        return None
    raw = Path(path or "")
    candidate = raw if raw.is_absolute() else ROOT_DIR / raw
    for target in {candidate, resolved}:
        try:
            st = os.lstat(target)
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("%s: 경로를 확인할 수 없어 거부함", name)
            return None
        attrs = getattr(st, "st_file_attributes", 0)
        if not stat.S_ISREG(st.st_mode) or attrs & _REPARSE_POINT:
            logger.warning("%s: 일반 파일이 아니어서 거부함(디렉터리·링크·reparse point)", name)
            return None
    return resolved


DATA_DIR = ROOT_DIR / "data"


def _resolve_data(path: str | None, *, name: str, default: Path) -> Path:
    """쓰기 대상 데이터 경로(SPACE_DOCS_FILE, CHROMA_DIR). _resolve 규칙 + 위치 제한.

    | 허용                                                        | 예                         |
    |-------------------------------------------------------------|----------------------------|
    | 저장소의 data/ 아래                                         | data/space_docs.json       |
    | ALLOW_EXTERNAL_PATHS=1 일 때 저장소 밖 절대 경로            | /srv/astrowatch/chroma     |
    거부(frontend/, backend/, 저장소 루트의 다른 파일 등)하면 경고 로그(값은 남기지 않음) 후 기본값을 쓴다.
    수집기와 색인 재구축이 이 경로에 파일을 쓰므로 정적 서빙 파일이나 소스를 덮어쓰지 못하게 한다.
    """
    resolved = _resolve(path, name=name)
    if resolved is None:
        return default
    if resolved != DATA_DIR and resolved.is_relative_to(DATA_DIR):
        return resolved
    if _env_flag("ALLOW_EXTERNAL_PATHS") and Path(path or "").is_absolute() and not resolved.is_relative_to(ROOT_DIR):
        return resolved
    logger.warning("%s: data/ 밖 경로를 거부하고 기본값을 씀 (저장소 밖 절대 경로는 ALLOW_EXTERNAL_PATHS=1 필요)", name)
    return default


def _origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(o.strip().rstrip("/") for o in raw.split(",") if o.strip())


@dataclass(frozen=True)
class Settings:
    # LLM (SSAFY GMS, OpenAI 호환)
    gms_key: str | None
    llm_provider: str              # anthropic(기본: GMS 의 Anthropic 호환 경로) | openai(GMS 의 OpenAI 호환 경로)
    openai_base_url: str
    llm_model: str
    llm_reasoning_effort: str      # openai 공급자 전용. gpt-6-luna 는 tools 사용 시 "none" 필수
    embedding_model: str

    # 외부 API 키
    data_go_kr_key: str | None
    kakao_rest_key: str | None
    openweather_key: str | None

    # 날씨 공급자: kma | mock
    weather_provider: str

    # Agent 실행 설정
    max_replans: int
    min_obs_minutes: int
    timezone: str

    # 저장소
    db_path: Path
    chroma_dir: Path

    # Google Calendar (심화 F3023)
    calendar_provider: str         # local | google
    google_client_secret_file: Path | None
    google_token_file: Path | None
    google_calendar_id: str

    # 서버
    app_host: str
    app_port: int
    app_allowed_origins: tuple[str, ...] = ()   # 터널 데모 등 추가 허용 출처
    app_allowed_hosts: tuple[str, ...] = ()     # Host 헤더 추가 허용 (TrustedHost)
    app_access_token: str | None = None         # 설정 시 /api/* 에 X-Astrowatch-Token 필요
    chat_rate_per_min: int = 12                 # /api/chat IP 당 분당 요청 수
    audit_rate_per_min: int = 6                 # POST /api/logs/{id}/audit IP 당 분당 요청 수 (LLM 진단 비용 보호)

    # 심화 F3020 (이상 징후) / F3021 (Planner/Worker) / F3022 (RAG)
    agent_structure: str = "planner_worker"     # planner_worker | classic (구조 변경 전후 비교용)
    worker_llm: bool = True                      # Worker 가 고칠 여지가 있는 실패(NO_RESULT 등)에만 LLM 판단
    worker_max_retry: int = 2                    # 같은 Tool 재시도 상한
    audit_llm: bool = True                       # 이상으로 판정한 실행에만 LLM 원인 진단
    audit_replan_threshold: int = 3              # 재계획이 이 횟수 이상이면 과다 재계획
    embedding_provider: str = "local"           # local(chromadb 내장 ONNX MiniLM) | gms(OpenAI 호환 임베딩 API)
    space_docs_file: Path = field(default=ROOT_DIR / "data/space_docs.json")  # 수집한 문서 원문(JSON)

    root_dir: Path = field(default=ROOT_DIR)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """CORS · CSRF 가드가 허용하는 출처 목록."""
        port = self.app_port
        origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
        if self.app_host not in ("0.0.0.0", "::", "", "127.0.0.1", "localhost"):
            origins.append(f"http://{self.app_host}:{port}")
        for o in self.app_allowed_origins:
            if o not in origins:
                origins.append(o)
        return tuple(origins)

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """TrustedHostMiddleware 허용 Host (포트 제외)."""
        hosts = ["127.0.0.1", "localhost"]
        if self.app_host not in ("0.0.0.0", "::", ""):
            hosts.append(self.app_host)
        for o in self.app_allowed_origins:  # 터널 출처의 호스트도 자동 허용
            h = urllib.parse.urlsplit(o).hostname
            if h:
                hosts.append(h)
        hosts.extend(self.app_allowed_hosts)
        return tuple(dict.fromkeys(h.lower() for h in hosts if h))

    @property
    def samples_dir(self) -> Path:
        return self.root_dir / "samples"

    @property
    def space_docs_cache_file(self) -> Path:
        """수집기 원문 캐시 (재실행 시 이어받기). space_docs_file 과 같은 폴더의 cache/ 아래라 같은 위치 제한을 받는다."""
        return self.space_docs_file.parent / "cache" / "space_docs_raw.json"


_LLM_BASE_URLS = {
    "anthropic": "https://gms.ssafy.io/gmsapi/api.anthropic.com/v1",
    "openai": "https://gms.ssafy.io/gmsapi/api.openai.com/v1/",
}
_LLM_DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-6-luna"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _llm_provider = (_env("LLM_PROVIDER", "anthropic") or "anthropic").lower()
    if _llm_provider not in _LLM_BASE_URLS:
        _llm_provider = "anthropic"
    return Settings(
        gms_key=_env("GMS_KEY") or _env("GMS_API_KEY"),   # 전환 가이드는 GMS_API_KEY 이름을 쓴다 — 둘 다 허용
        llm_provider=_llm_provider,
        openai_base_url=_env("OPENAI_BASE_URL", _LLM_BASE_URLS[_llm_provider]) or _LLM_BASE_URLS[_llm_provider],
        llm_model=_env("LLM_MODEL", _LLM_DEFAULT_MODELS[_llm_provider]) or _LLM_DEFAULT_MODELS[_llm_provider],
        llm_reasoning_effort=_env("LLM_REASONING_EFFORT", "none") or "none",
        embedding_model=_env("EMBEDDING_MODEL", "text-embedding-3-small") or "text-embedding-3-small",
        data_go_kr_key=_data_go_kr_key(),
        kakao_rest_key=_env("KAKAO_REST_KEY"),
        openweather_key=_env("OPENWEATHER_KEY"),
        weather_provider=(_env("WEATHER_PROVIDER", "kma") or "kma").lower(),
        max_replans=_env_int("MAX_REPLANS", 3),
        min_obs_minutes=_env_int("MIN_OBS_MINUTES", 30),
        timezone=_env("TIMEZONE", "Asia/Seoul") or "Asia/Seoul",
        db_path=_resolve(_env("DB_PATH", "data/astrowatch.db"), name="DB_PATH") or ROOT_DIR / "data/astrowatch.db",
        chroma_dir=_resolve_data(_env("CHROMA_DIR", "data/chroma"), name="CHROMA_DIR", default=ROOT_DIR / "data/chroma"),
        calendar_provider=(_env("CALENDAR_PROVIDER", "local") or "local").lower(),
        google_client_secret_file=_resolve_secret(_env("GOOGLE_CLIENT_SECRET_FILE"), name="GOOGLE_CLIENT_SECRET_FILE"),
        google_token_file=_resolve_secret(_env("GOOGLE_TOKEN_FILE"), name="GOOGLE_TOKEN_FILE"),
        google_calendar_id=_env("GOOGLE_CALENDAR_ID", "primary") or "primary",
        app_host=_env("APP_HOST", "127.0.0.1") or "127.0.0.1",
        app_port=_env_int("APP_PORT", 8000),
        # 배포 주소: APP_PUBLIC_URL(직접 지정) 과 Render 가 주는 RENDER_EXTERNAL_URL 은 허용 출처/Host 에 자동 포함
        app_allowed_origins=_origins(",".join(v for v in (_env("APP_ALLOWED_ORIGINS"), _env("APP_PUBLIC_URL"), _env("RENDER_EXTERNAL_URL")) if v)),
        app_allowed_hosts=tuple(h.strip() for h in (_env("APP_ALLOWED_HOSTS") or "").split(",") if h.strip()),
        app_access_token=_env("APP_ACCESS_TOKEN"),
        chat_rate_per_min=max(1, _env_int("CHAT_RATE_PER_MIN", 12)),
        audit_rate_per_min=max(1, _env_int("AUDIT_RATE_PER_MIN", 6)),
        agent_structure=("classic" if (_env("AGENT_STRUCTURE", "planner_worker") or "").lower() == "classic" else "planner_worker"),
        worker_llm=_env_bool("WORKER_LLM", True),
        worker_max_retry=max(0, _env_int("WORKER_MAX_RETRY", 2)),
        audit_llm=_env_bool("AUDIT_LLM", True),
        audit_replan_threshold=max(1, _env_int("AUDIT_REPLAN_THRESHOLD", 3)),
        embedding_provider=("gms" if (_env("EMBEDDING_PROVIDER", "local") or "").lower() == "gms" else "local"),
        space_docs_file=_resolve_data(_env("SPACE_DOCS_FILE", "data/space_docs.json"), name="SPACE_DOCS_FILE",
                                      default=ROOT_DIR / "data/space_docs.json"),
    )


def reset_settings_cache() -> None:
    """테스트에서 환경변수를 바꾼 뒤 호출한다."""
    get_settings.cache_clear()
