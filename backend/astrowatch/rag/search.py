"""하이브리드 검색 (F3022). VastHorizon 2회차 search.py/retrieval.py 에서 이식 (정확 비교 재계산, 문서별 중복 제거).

점수 = 코사인 유사도 + 이름 가산 + 글자 2-gram 겹침 x LEX_WEIGHT

| 단계 | 내용 |
|------|------|
| 1 | 이름 가산: 질문이 카탈로그 천체(catalog.match_object)이거나 문서 제목/별칭을 포함하면 그 문서에 가산 |
| 2 | 벡터 검색: Chroma 코사인 거리로 상위 후보 (n = max(8k, 32)) |
| 3 | 가산 받은 문서는 벡터 순위와 상관없이 그 문서의 청크 전부를 정확 비교(코사인)로 다시 계산해 후보에 넣는다 |
| 4 | 문서마다 가장 높은 청크 하나만 남기고(중복 제거) 점수 순 상위 k 개 |
| 5 | 이름이 맞지 않고 점수가 공급자별 최소 점수(local 0.75, 그 밖 0.35) 미만인 문서는 버린다. 남은 것이 없으면 NO_RESULT |

한국어에 약한 영어 임베딩(all-MiniLM-L6-v2)을 써도 천체/용어 이름이 맞으면 그 문서가 먼저 오도록 이름 가산을 둔다.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from astrowatch.rag import store

MIN_SCORE = 0.35            # 이보다 낮고 이름도 일치하지 않으면 '관련 문서 없음' (gms 등)
# 공급자별 최소 점수. 영어 모델 all-MiniLM-L6-v2 는 관련 없는 한국어 문장끼리도 코사인이 0.6 안팎이라 높게 잡는다
# (실측: '피자 레시피 알려줘' 최고 0.655, '유성우는 왜 생겨?' 의 유성우 0.90)
MIN_SCORE_BY_PROVIDER = {"local": 0.75}
BOOST_BEST = 0.35           # 가장 길게 일치한 이름(또는 카탈로그 일치)의 문서
BOOST_OTHER = 0.15          # 그 밖에 이름이 일치한 문서
LEX_WEIGHT = 0.2
SNIPPET_MAX = 400

# 질문 꼬리말 (2-gram 겹침에서 뺀다)
_QUESTION_TAIL = re.compile(r"(이|가)?\s*(뭐야|뭔가요|무엇인가요|무엇이야|뭐지|어떤\s*천체야|란\s*뭐야|이란|란)\??$")
_ONE_CHAR_PARTICLES = ("이란", "란", "이", "은", "는", "에", "의", "을", "가")


@dataclass
class Hit:
    title: str
    source_url: str
    snippet: str
    score: float
    chunk_id: str
    license: str
    section: str = ""
    name_match: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def norm(text: str) -> str:
    return "".join((text or "").split()).lower()


def _base_title(title: str) -> str:
    return norm(re.sub(r"\s*\(.*?\)\s*", "", title or ""))


def _alias_hit(alias: str, q: str) -> bool:
    """alias 가 질문 q(공백 제거) 안에 이름으로 들어 있는가. 한 글자 이름(합, 충)은 질문 맨 앞 + 조사일 때만."""
    if len(alias) >= 2:
        if alias not in q:
            return False
        from astrowatch.astro.catalog import _bounded

        return _bounded(q, alias)
    return any(q.startswith(alias + p) or f"의{alias}{p}" in q for p in _ONE_CHAR_PARTICLES)


def name_boosts(query: str, table: dict[int, dict[str, Any]]) -> dict[int, float]:
    """doc_index → 가산점. 카탈로그 일치 + 제목/별칭 포함 (가장 긴 일치가 BOOST_BEST)."""
    q = norm(query)
    lengths: dict[int, int] = {}
    for idx, d in table.items():
        names = {norm(a) for a in d["aliases"]} | {_base_title(d["title"]), norm(d["title"])}
        best = max((len(a) for a in names if a and _alias_hit(a, q)), default=0)
        if best:
            lengths[idx] = best
    try:
        from astrowatch.astro.catalog import find_object

        obj = find_object(query)
    except Exception:  # noqa: BLE001 (카탈로그를 못 읽어도 검색은 계속)
        obj = None
    if obj is not None:
        for idx, d in table.items():
            if obj["id"] in d["catalog_ids"]:
                lengths[idx] = max(lengths.get(idx, 0), 10_000)   # 카탈로그 일치는 가장 강한 근거
    if not lengths:
        return {}
    top = max(lengths.values())
    return {idx: (BOOST_BEST if n == top else BOOST_OTHER) for idx, n in lengths.items()}


def _bigrams(text: str) -> set[str]:
    t = norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def lexical(query: str, body: str) -> float:
    q = _bigrams(_QUESTION_TAIL.sub("", (query or "").strip()))
    if not q:
        return 0.0
    return len(q & _bigrams(body)) / len(q)


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def body_of(document: str) -> str:
    """임베딩 텍스트에서 머리말 '[제목 > 절]' 줄을 뺀 본문."""
    if document.startswith("[") and "\n" in document:
        return document.split("\n", 1)[1]
    return document


def snippet(text: str, limit: int = SNIPPET_MAX) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    i = max(cut.rfind("다. "), cut.rfind(". "))
    if i >= limit * 0.5:
        return cut[: i + 1].strip() + "…"
    return cut.rstrip() + "…"


def search(query: str, k: int = 4, embedder: store.Embedder | None = None) -> tuple[list[Hit], dict[str, Any]]:
    """(상위 k 개 Hit, 색인 정보). 색인 없음/불일치는 store 의 예외를 그대로 올린다."""
    if embedder is None:
        try:
            embedder = store.get_embedder()
        except (store.IndexMissingError, store.IndexMismatchError):
            raise
        except Exception as e:  # noqa: BLE001 (공급자 준비 실패는 색인을 쓸 수 없음으로 안내)
            raise store.IndexMissingError("임베딩 공급자를 준비하지 못함") from e
    col, meta = store.open_index(embedder)
    count = col.count()
    info = store.index_info(meta, count)
    table = store.doc_table(str(store.store_path()), meta.get("built_at"))
    boosts = name_boosts(query, table)

    qvec = embedder.embed([query])[0]
    sd = meta.get("embedding_dim")
    if sd and int(sd) != len(qvec):
        raise store.IndexMismatchError(f"임베딩 차원 불일치(색인 {sd}, 질문 {len(qvec)}). {store.REBUILD_HINT}")

    # chunk_id → (cosine, metadata, document)
    cands: dict[str, tuple[float, dict[str, Any], str]] = {}
    n = min(count, max(k * 8, 32))
    res = col.query(query_embeddings=[qvec], n_results=n, include=["distances", "metadatas", "documents"])
    for cid, dist, m, doc in zip(res["ids"][0], res["distances"][0], res["metadatas"][0], res["documents"][0]):
        cands[cid] = (1.0 - float(dist), m, doc)
    if boosts:
        got = col.get(where={"doc_index": {"$in": list(boosts)}}, include=["embeddings", "metadatas", "documents"])
        for cid, vec, m, doc in zip(got["ids"], got["embeddings"], got["metadatas"], got["documents"]):
            cands[cid] = (_cos(qvec, [float(x) for x in vec]), m, doc)

    best: dict[int, Hit] = {}
    for cid, (cos, m, doc) in cands.items():
        idx = int(m["doc_index"])
        body = body_of(doc)
        boost = boosts.get(idx, 0.0)
        score = cos + boost + LEX_WEIGHT * lexical(query, body)
        if idx in best and best[idx].score >= score:
            continue
        best[idx] = Hit(title=m.get("title") or "", source_url=m.get("source_url") or "", snippet=snippet(body),
                        score=round(score, 3), chunk_id=cid, license=m.get("license") or "",
                        section=m.get("section") or "", name_match=boost > 0)
    hits = sorted(best.values(), key=lambda h: -h.score)
    floor = MIN_SCORE_BY_PROVIDER.get(str(meta.get("embedding_provider")), MIN_SCORE)
    hits = [h for h in hits if h.name_match or h.score >= floor][:k]   # 이름이 맞은 문서는 점수와 상관없이 근거
    return hits, info
