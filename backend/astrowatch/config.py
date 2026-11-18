"""실행 설정 — .env 에서 읽는다. 키는 소스에 적지 않는다 (NF3001).

로드 순서: 저장소 루트의 .env → OS 환경변수. 둘 다 없으면 None/기본값.
키가 없어도 import 는 실패하지 않는다. 키가 필요한 Tool 이 AUTH 실패를 돌려준다 (NF3003).
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# backend/astrowatch/config.py → 저장소 루트
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env", override=False)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


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


def _resolve(path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    return p if p.is_absolute() else ROOT_DIR / p


@dataclass(frozen=True)
class Settings:
    # LLM (SSAFY GMS, OpenAI 호환)
    gms_key: str | None
    openai_base_url: str
    llm_model: str
    llm_reasoning_effort: str      # gpt-6-luna 는 tools 사용 시 "none" 필수
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

    root_dir: Path = field(default=ROOT_DIR)

    @property
    def samples_dir(self) -> Path:
        return self.root_dir / "samples"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        gms_key=_env("GMS_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1/") or "",
        llm_model=_env("LLM_MODEL", "gpt-6-luna") or "gpt-6-luna",
        llm_reasoning_effort=_env("LLM_REASONING_EFFORT", "none") or "none",
        embedding_model=_env("EMBEDDING_MODEL", "text-embedding-3-small") or "text-embedding-3-small",
        data_go_kr_key=_data_go_kr_key(),
        kakao_rest_key=_env("KAKAO_REST_KEY"),
        openweather_key=_env("OPENWEATHER_KEY"),
        weather_provider=(_env("WEATHER_PROVIDER", "kma") or "kma").lower(),
        max_replans=_env_int("MAX_REPLANS", 3),
        min_obs_minutes=_env_int("MIN_OBS_MINUTES", 30),
        timezone=_env("TIMEZONE", "Asia/Seoul") or "Asia/Seoul",
        db_path=_resolve(_env("DB_PATH", "data/astrowatch.db")) or ROOT_DIR / "data/astrowatch.db",
        chroma_dir=_resolve(_env("CHROMA_DIR", "data/chroma")) or ROOT_DIR / "data/chroma",
        calendar_provider=(_env("CALENDAR_PROVIDER", "local") or "local").lower(),
        google_client_secret_file=_resolve(_env("GOOGLE_CLIENT_SECRET_FILE")),
        google_token_file=_resolve(_env("GOOGLE_TOKEN_FILE")),
        google_calendar_id=_env("GOOGLE_CALENDAR_ID", "primary") or "primary",
        app_host=_env("APP_HOST", "127.0.0.1") or "127.0.0.1",
        app_port=_env_int("APP_PORT", 8000),
    )


def reset_settings_cache() -> None:
    """테스트에서 환경변수를 바꾼 뒤 호출한다."""
    get_settings.cache_clear()
