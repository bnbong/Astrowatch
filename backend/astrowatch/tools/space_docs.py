"""심화 F3022 search_space_docs: 천문 배경 지식 문서(Vector DB) 검색 Tool (로컬).

색인: data/chroma/space_docs (scripts/collect_space_docs.py → scripts/build_space_docs.py).
문서: 한국어 위키백과 extract (CC BY-SA 4.0). 근거 문단과 출처 URL 을 함께 돌려준다.

실패 코드
| 코드           | 언제                                                          |
|----------------|---------------------------------------------------------------|
| NO_INDEX       | 색인 폴더/컬렉션이 없거나 비었음, chromadb/임베딩 모델 사용 불가 |
| INDEX_MISMATCH | 색인의 임베딩 모델/차원이 현재 EMBEDDING_PROVIDER 설정과 다름    |
| NO_RESULT      | 색인은 있지만 최소 점수(MIN_SCORE) 이상인 문서가 없음            |

chromadb 는 함수 안에서 import 한다. 설치돼 있지 않아도 Tool 등록과 /api/tools 목록은 그대로 동작한다.
"""

from __future__ import annotations

from astrowatch.tools.base import ErrorCode, ToolKind, ToolResult, fail, ok
from astrowatch.tools.contracts import SpaceDocsHit, SpaceDocsIndex, SpaceDocsInput, SpaceDocsOutput
from astrowatch.tools.registry import registry

NO_INDEX_MESSAGE = "색인이 아직 없습니다. scripts/collect_space_docs.py 와 scripts/build_space_docs.py 를 실행하세요"
UNAVAILABLE_MESSAGE = ("색인을 쓸 수 없습니다(chromadb 또는 임베딩 모델을 불러오지 못함). "
                       "scripts/build_space_docs.py 를 실행한 환경과 EMBEDDING_PROVIDER 설정을 확인하세요")
MISMATCH_MESSAGE = ("색인의 임베딩 모델이 현재 설정(EMBEDDING_PROVIDER)과 다릅니다. "
                    "scripts/build_space_docs.py 로 색인을 다시 만드세요")
NO_RESULT_MESSAGE = "색인에서 질문과 관련된 문서를 찾지 못했습니다"


@registry.tool(
    name="search_space_docs",
    description=(
        "천체나 천문 현상의 정의, 원리, 역사, 특징처럼 외부 API(천문현상 목록, 박명, 날씨, 장소)로 확인할 수 없는 "
        "배경 지식을 물을 때 쓴다 (예: '유성우는 왜 생겨?', '안드로메다 은하는 어떤 천체야?', '천문박명이 뭐야?'). "
        "한국어 위키백과 문서 색인(Vector DB)에서 근거 문단과 출처 URL 을 찾아 준다. "
        "언제 보이는지, 몇 시인지, 날씨는 이 Tool 이 아니라 다른 Tool 로 계산한다. 색인이 없으면 NO_INDEX."
    ),
    kind=ToolKind.LOCAL,
    input_model=SpaceDocsInput,
    output_model=SpaceDocsOutput,
)
def search_space_docs(args: SpaceDocsInput) -> ToolResult[SpaceDocsOutput]:
    try:
        from astrowatch.rag import search as S
        from astrowatch.rag.store import IndexMismatchError, IndexMissingError
    except Exception:  # noqa: BLE001 (의존성 문제도 색인 없음으로 안내)
        return fail(ErrorCode.NO_INDEX, UNAVAILABLE_MESSAGE)
    try:
        hits, info = S.search(args.query, args.k)
    except IndexMismatchError:
        return fail(ErrorCode.INDEX_MISMATCH, MISMATCH_MESSAGE)
    except IndexMissingError as e:
        missing = str(e) in ("색인 폴더 없음", "컬렉션 없음", "컬렉션이 비어 있음")
        return fail(ErrorCode.NO_INDEX, NO_INDEX_MESSAGE if missing else UNAVAILABLE_MESSAGE)
    if not hits:
        return fail(ErrorCode.NO_RESULT, NO_RESULT_MESSAGE)
    return ok(SpaceDocsOutput(hits=[SpaceDocsHit(**h.to_dict()) for h in hits], index=SpaceDocsIndex(**info)),
              note="출처: 한국어 위키백과 (CC BY-SA 4.0), 문서 앞부분 발췌")
