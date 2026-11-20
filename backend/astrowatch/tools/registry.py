"""Tool 레지스트리 (F3001) — 이름으로 등록하고, Agent 에는 명세만 넘긴다."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from pydantic import BaseModel

from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, ToolSpec, fail


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"Tool 이름 중복: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def tool(
        self,
        *,
        name: str,
        description: str,
        kind: ToolKind,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        fixed_arg_names: Iterable[str] = (),
    ) -> Callable[[Callable[[Any], ToolResult[Any]]], ToolSpec]:
        """데코레이터. 핸들러는 (args: input_model) -> ToolResult 형태."""

        def decorator(fn: Callable[[Any], ToolResult[Any]]) -> ToolSpec:
            spec = ToolSpec(
                name=name,
                description=description,
                kind=kind,
                input_model=input_model,
                output_model=output_model,
                handler=fn,
                fixed_arg_names=tuple(fixed_arg_names),
            )
            return self.register(spec)

        return decorator

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, kind: ToolKind | None = None) -> list[ToolSpec]:
        return [s for s in self._tools.values() if kind is None or s.kind == kind]

    def openai_tools(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        chosen = set(names) if names is not None else None
        return [s.to_openai_tool() for s in self._tools.values() if chosen is None or s.name in chosen]

    def invoke(self, name: str, raw_args: dict[str, Any]) -> ToolResult[Any]:
        spec = self._tools.get(name)
        if spec is None:
            return fail(ErrorCode.BAD_INPUT, f"등록되지 않은 Tool: {name}")
        return spec.invoke(raw_args)

    def describe(self) -> list[dict[str, Any]]:
        """검증 4.1 용: 이름 · 설명 · 구분 · 입력 스키마 목록."""
        return [
            {
                "name": s.name,
                "kind": s.kind.value,
                "description": s.description,
                "input_schema": s.input_model.model_json_schema(),
                "fixed_args": list(s.fixed_arg_names),
            }
            for s in self._tools.values()
        ]

    def clear(self) -> None:
        self._tools.clear()


registry = ToolRegistry()


def load_builtin_tools() -> ToolRegistry:
    """내장 Tool 8개를 import 해 등록한다. 앱 시작과 테스트에서 한 번 호출.

    모두 완성된 내장 모듈이라 import 오류는 그대로 올린다 (코드 오류를 "기능 없음"으로 숨기지 않는다).
    키, 색인, Google 인증, chromadb 같은 실행 환경 문제는 import 시점이 아니라 Tool 호출 때
    Tool 오류 계약(AUTH, NO_INDEX 등)으로 돌려준다. 무거운 선택 의존성은 각 Tool 함수 안에서 지연 import.
    """
    from astrowatch.tools import (  # noqa: F401 (import 시 registry 에 등록)
        calendar,
        night_window,
        observatory,
        sky_events,
        sky_forecast,
        sky_object,
        space_docs,
    )

    return registry
