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
    """필수 Tool 4개(+심화)를 import 해 등록한다. 앱 시작과 테스트에서 한 번 호출."""
    import importlib

    for mod in ("sky_events", "night_window", "sky_forecast", "observatory"):
        importlib.import_module(f"astrowatch.tools.{mod}")
    for mod in ("space_docs", "calendar", "sky_object"):  # 심화 — 없으면 건너뜀
        try:
            importlib.import_module(f"astrowatch.tools.{mod}")
        except ModuleNotFoundError:
            pass
    return registry
