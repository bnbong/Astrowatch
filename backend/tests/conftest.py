"""테스트 공통 — 키 없이 돌아가야 한다. samples/ 의 실제 응답을 fixture 로 쓴다."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """실제 키·DB 를 건드리지 않는다. 각 테스트는 빈 환경 + 임시 DB 로 시작."""
    for key in ("GMS_KEY", "DATA_GO_KR_KEY", "KAKAO_REST_KEY", "OPENWEATHER_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WEATHER_PROVIDER", "mock")
    monkeypatch.setenv("CALENDAR_PROVIDER", "local")
    from astrowatch.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def sample():
    def _load(name: str) -> str:
        path = SAMPLES / name
        if not path.exists():
            pytest.skip(f"샘플 없음: {name}")
        return path.read_text(encoding="utf-8")

    return _load


@pytest.fixture
def fresh_registry():
    from astrowatch.tools.registry import registry

    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def fresh_bus():
    from astrowatch.events.bus import bus

    bus.clear()
    yield bus
    bus.clear()
