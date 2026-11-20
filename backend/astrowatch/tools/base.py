"""Tool 공통 계약 (F3001).

모든 Tool 은 같은 봉투로 답한다.
    성공: {"ok": true,  "data": {...}, "note": "추정이 섞였다" (선택)}
    실패: {"ok": false, "error": {"code": "AUTH", "message": "..."}}
예외는 밖으로 던지지 않는다. 실행 흐름은 ok 만 보고 다음 단계로 간다 (NF3003).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ToolKind(str, Enum):
    EXTERNAL = "external"   # 외부 API 를 감싼 Tool (필수 구현 대상)
    LOCAL = "local"         # 이 서버의 기능을 감싼 Tool (심화)


class ErrorCode(str, Enum):
    """실패 코드. 재계획 여부를 가르는 기준이기도 하다.

    - 재시도해도 같은 결과(AUTH, BAD_INPUT, OUT_OF_RANGE)는 재계획하지 않고 바로 안내한다.
    - NO_RESULT, NO_WINDOW 는 '그 입력으로는 없다'는 뜻이라 대안 탐색(재계획)의 근거가 된다.
    - UPSTREAM, TIMEOUT 은 일시 장애. 사유를 남기고 다음 단계로 간다.
    """

    AUTH = "AUTH"                   # 키 누락·인증 실패
    BAD_INPUT = "BAD_INPUT"         # 필수 인자 누락·형식 오류
    NO_RESULT = "NO_RESULT"         # 조회 결과 없음
    NO_WINDOW = "NO_WINDOW"         # 박명·출몰 값이 비어 관측 창을 계산할 수 없음
    OUT_OF_RANGE = "OUT_OF_RANGE"   # 예보 범위 밖 등 제공 범위 밖
    NO_INDEX = "NO_INDEX"           # RAG 색인 없음, chromadb/임베딩 모델 사용 불가 (심화 F3022)
    INDEX_MISMATCH = "INDEX_MISMATCH"  # RAG 색인의 임베딩 모델/차원이 현재 설정과 다름 (심화 F3022)
    NOT_FOUND = "NOT_FOUND"         # 저장소에 대상 없음 (심화 캘린더)
    UPSTREAM = "UPSTREAM"           # 외부 API 오류 응답
    TIMEOUT = "TIMEOUT"             # 외부 API 시간 초과
    INTERNAL = "INTERNAL"           # Tool 안의 예외 (던지지 않고 변환)

    @property
    def retriable(self) -> bool:
        """같은 Tool 을 인자 바꿔 다시 부르면 결과가 달라질 수 있는가."""
        return self in {ErrorCode.NO_RESULT, ErrorCode.NO_WINDOW, ErrorCode.BAD_INPUT}

    @property
    def fatal(self) -> bool:
        """다시 시도해도 같은 결과 — 재계획하지 않고 바로 안내한다.

        NO_INDEX, INDEX_MISMATCH 는 키 문제가 아니라 색인 준비 문제라 여기 넣지 않는다
        (재계획 대상도 아니다: retriable 아님. 응답이 색인 안내 문장으로 바로 답한다)."""
        return self in {ErrorCode.AUTH, ErrorCode.OUT_OF_RANGE}


class ToolError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str = Field(description="사용자에게 보여도 되는 짧은 설명. 키·URL·스택은 넣지 않는다")


class ToolResult(BaseModel, Generic[T]):
    """Tool 응답 봉투. data 와 error 중 하나만 채워진다."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    data: T | None = None
    note: str | None = Field(default=None, description="추정·범위 밖 등 사용자에게 밝혀야 할 사실")
    error: ToolError | None = None

    @property
    def code(self) -> ErrorCode | None:
        return self.error.code if self.error else None

    def as_event_payload(self, max_chars: int = 2000) -> dict[str, Any]:
        """이벤트에 실을 요약. 원본 전체 대신 길이를 제한한 JSON 을 남긴다."""
        payload: dict[str, Any] = {"ok": self.ok}
        if self.note:
            payload["note"] = self.note
        if self.error:
            payload["error"] = self.error.model_dump(mode="json")
        if self.data is not None:
            text = self.model_dump_json(include={"data"})
            payload["data"] = text if len(text) <= max_chars else text[:max_chars] + "…(truncated)"
        return payload


def ok(data: T, note: str | None = None) -> ToolResult[T]:
    return ToolResult(ok=True, data=data, note=note)


def fail(code: ErrorCode, message: str) -> ToolResult[Any]:
    return ToolResult(ok=False, error=ToolError(code=code, message=message))


class ToolSpec(BaseModel):
    """Agent 에게 넘기는 명세. Agent 는 description 만 보고 Tool 을 고르므로
    description 에는 '무엇을 하는지'가 아니라 '언제 쓰는지'를 적는다."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    kind: ToolKind
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[BaseModel], ToolResult[Any]]
    fixed_arg_names: tuple[str, ...] = Field(
        default=(),
        description="사용자 조건으로 채워져 Agent 가 임의로 바꾸면 안 되는 인자 (재계획·Worker 재시도에서 고정)",
    )

    def to_openai_tool(self) -> dict[str, Any]:
        """OpenAI function-calling 형식. LLM 에는 이름·설명·입력 스키마만 넘긴다."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }

    def invoke(self, raw_args: dict[str, Any]) -> ToolResult[Any]:
        """인자 검증 → 핸들러 호출. 어떤 예외도 밖으로 나가지 않는다."""
        try:
            args = self.input_model.model_validate(raw_args)
        except Exception as e:  # pydantic ValidationError 포함
            return fail(ErrorCode.BAD_INPUT, f"입력 형식 오류: {_short(e)}")
        try:
            result = self.handler(args)
        except Exception as e:  # noqa: BLE001 — Tool 안의 모든 예외를 봉투로 변환
            return fail(ErrorCode.INTERNAL, f"Tool 내부 오류: {_short(e)}")
        if not isinstance(result, ToolResult):
            return fail(ErrorCode.INTERNAL, f"{self.name} 가 ToolResult 가 아닌 값을 돌려줌")
        return result


def _short(e: Exception, limit: int = 120) -> str:
    text = f"{type(e).__name__}: {e}".replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"
