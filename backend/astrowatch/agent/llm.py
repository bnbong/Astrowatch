"""LLM 연결 — langchain-openai 의 ChatOpenAI 로 SSAFY GMS 를 호출한다. 공급자 경로는 두 가지.

- anthropic (기본): GMS 의 Anthropic 호환 경로. 모델 claude-haiku-5-5 (1회 1크레딧).
  base_url https://gms.ssafy.io/gmsapi/api.anthropic.com/v1, 헤더 x-api-key 에도 키를 넣는다(전환 가이드).
  reasoning_effort 같은 OpenAI 전용 파라미터는 보내지 않는다. structured output 은 function_calling 이 안전하다.
- openai: GMS 의 OpenAI 호환 경로(gpt-6-luna). tools 를 쓰려면 reasoning_effort="none",
  max_tokens 대신 max_completion_tokens 만 허용 → 토큰 한도를 직접 넣지 않는다. temperature 도 주지 않는다.
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


def structured_methods() -> tuple[str, ...]:
    """with_structured_output 에 시도할 방식 순서 (공급자별)."""
    if _override is not None:
        return ("json_schema", "function_calling")
    if get_settings().llm_provider == "anthropic":
        return ("function_calling", "json_mode")
    return ("json_schema", "function_calling")


def get_chat_model() -> Any:
    if _override is not None:
        return _override
    from langchain_openai import ChatOpenAI

    s = get_settings()
    common: dict[str, Any] = {"model": s.llm_model, "base_url": s.openai_base_url, "api_key": s.gms_key,
                              "timeout": 60, "max_retries": 1}
    if s.llm_provider == "anthropic":
        return ChatOpenAI(default_headers={"x-api-key": s.gms_key or ""}, **common)
    return ChatOpenAI(reasoning_effort=s.llm_reasoning_effort, **common)
