"""Tool 영역 — 외부 API 툴과 로컬 서버 API 툴.

구현 모듈은 import 시 registry 에 자신을 등록한다. 등록 순서가 Agent 에게 보이는 순서다.
"""

from astrowatch.tools.base import (  # noqa: F401
    ErrorCode,
    ToolError,
    ToolKind,
    ToolResult,
    ToolSpec,
    fail,
    ok,
)
from astrowatch.tools.registry import ToolRegistry, registry  # noqa: F401
