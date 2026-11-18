"""LLM 연결 — langchain-openai + SSAFY GMS (OpenAI 호환).

주의 (gpt-6-luna):
- chat/completions 에서 tools 를 쓰려면 reasoning_effort="none" 이어야 한다.
- max_tokens 대신 max_completion_tokens 만 허용 → 여기서 토큰 한도를 직접 넣지 않는다.
- temperature 를 주지 않는다.
"""

from __future__ import annotations

from typing import Any

from astrowatch.config import get_settings

_override: Any | None = None


def set_chat_model_override(model: Any | None) -> None:
    """테스트용: get_chat_model() 이 돌려줄 객체를 바꾼다. None 이면 해제."""
    global _override
    _override = model


def llm_available() -> bool:
    return _override is not None or bool(get_settings().gms_key)


def model_name() -> str:
    if _override is not None:
        return getattr(_override, "model_name", None) or type(_override).__name__
    return get_settings().llm_model


def get_chat_model() -> Any:
    if _override is not None:
        return _override
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model,
        base_url=s.openai_base_url,
        api_key=s.gms_key,
        reasoning_effort=s.llm_reasoning_effort,
        timeout=60,
        max_retries=1,
    )
