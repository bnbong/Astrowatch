"""Chroma 색인 저장소와 임베딩 공급자 (F3022). VastHorizon 2회차 chroma.py 에서 이식.

임베딩 공급자 (settings.embedding_provider)
| 값    | 구현                                                        | 모델 식별              | 키   |
|-------|-------------------------------------------------------------|------------------------|------|
| local | chromadb 내장 ONNX DefaultEmbeddingFunction (최초 1회 내려받음) | local:all-MiniLM-L6-v2 | 없음 |
| gms   | OpenAI 호환 임베딩 API (SSAFY GMS)                          | gms:<embedding_model>  | GMS_KEY |

임베딩 식별 검사 (VastHorizon 방식)
- 컬렉션 메타데이터에 embedding_provider, embedding_model, embedding_dim, built_at, chunks, docs 를 기록한다.
  키, base_url 같은 비밀/환경 값은 기록하지 않는다.
- 검색할 때 현재 공급자의 식별 정보와 비교해 다르면 IndexMismatchError (다른 벡터 공간으로 검색하지 않는다).

재구축: 임시 컬렉션(space_docs__staging)에 처음부터 만들고 건수를 검증한 뒤 바꾼다.
  1) 활성 컬렉션을 space_docs__previous 로 이름을 바꿔 남겨 둔다
  2) 임시 컬렉션을 space_docs 로 바꾼다. 실패하면 1) 의 컬렉션을 다시 space_docs 로 되돌린다
  3) 성공한 뒤에만 space_docs__previous 를 지운다 (어느 단계에서 실패해도 이전 색인이 남는다)
초기화 실패: chromadb import, PersistentClient 생성, 컬렉션 열기, 임베딩 함수(ONNX 모델) 준비에서 나는 예외는
모두 IndexMissingError 로 바꾼다 (Tool: NO_INDEX). 식별 정보 불일치만 IndexMismatchError 로 따로 둔다.
저장 위치: settings.chroma_dir / "space_docs" (컬렉션별 저장소 분리, VastHorizon 의 교훈).

테스트 훅: set_embedder_override(embedder) 로 네트워크/모델 없이 가짜 임베딩을 끼운다.
embedder 는 provider, model, dim 속성과 embed(texts) -> list[list[float]] 메서드만 있으면 된다.
"""

from __future__ import annotations

import datetime as dt
import functools
from pathlib import Path
from typing import Any, Callable, Protocol

from astrowatch.config import get_settings
from astrowatch.rag.chunking import Chunk, chunk_document

COLLECTION = "space_docs"
STAGING = COLLECTION + "__staging"
PREVIOUS = COLLECTION + "__previous"
LOCAL_MODEL = "all-MiniLM-L6-v2"
LOCAL_DIM = 384
GMS_EMBEDDINGS_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1/embeddings"
BUILD_HINT = "scripts/collect_space_docs.py 와 scripts/build_space_docs.py 를 실행하세요"
REBUILD_HINT = "scripts/build_space_docs.py 로 색인을 다시 만드세요"


class IndexMissingError(RuntimeError):
    """색인이 없거나 비었거나, chromadb/임베딩 모델을 쓸 수 없을 때 (Tool: NO_INDEX)."""


class IndexMismatchError(RuntimeError):
    """색인의 임베딩 식별 정보가 현재 설정과 다를 때 (Tool: INDEX_MISMATCH)."""


class Embedder(Protocol):
    provider: str
    model: str
    dim: int | None

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """chromadb 내장 ONNX all-MiniLM-L6-v2. 첫 호출 때 모델을 chroma 캐시로 내려받는다."""

    provider = "local"
    model = LOCAL_MODEL
    dim: int | None = LOCAL_DIM

    def __init__(self) -> None:
        self._fn: Any = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._fn is None:
            try:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            except Exception as e:  # noqa: BLE001
                raise IndexMissingError("chromadb 를 불러오지 못함") from e
            try:
                self._fn = DefaultEmbeddingFunction()
            except Exception as e:  # noqa: BLE001 (onnxruntime 없음, 모델 내려받기 실패 등)
                raise IndexMissingError("로컬 임베딩 모델을 불러오지 못함") from e
        try:
            return [[float(x) for x in v] for v in self._fn(texts)]
        except Exception as e:  # noqa: BLE001 (모델 내려받기 실패 등)
            raise IndexMissingError("로컬 임베딩 모델을 불러오지 못함") from e


class GmsEmbedder:
    """OpenAI 호환 임베딩 API. 키는 헤더로만 보내고 기록하지 않는다."""

    provider = "gms"
    dim: int | None = None
    BATCH = 64

    def __init__(self, model: str, key: str | None) -> None:
        self.model = model
        self._key = key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._key:
            raise IndexMissingError("GMS 임베딩 키가 없음")
        import requests

        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            try:
                resp = requests.post(GMS_EMBEDDINGS_URL, timeout=60,
                                     headers={"Authorization": f"Bearer {self._key}"},
                                     json={"model": self.model, "input": texts[i:i + self.BATCH]})
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
            except Exception as e:  # noqa: BLE001 (상세는 키가 섞일 수 있어 남기지 않는다)
                raise IndexMissingError(f"GMS 임베딩 호출 실패({type(e).__name__})") from None
            out.extend([float(x) for x in d["embedding"]] for d in data)
        if out and self.dim is None:
            self.dim = len(out[0])
        return out


_override: Embedder | None = None


def set_embedder_override(embedder: Embedder | None) -> None:
    """테스트 훅: get_embedder() 가 돌려줄 객체를 바꾼다. None 이면 해제."""
    global _override
    _override = embedder


def get_embedder(provider: str | None = None) -> Embedder:
    if _override is not None:
        return _override
    s = get_settings()
    provider = (provider or s.embedding_provider or "local").lower()
    if provider == "gms":
        return GmsEmbedder(s.embedding_model, s.gms_key)
    return _local_embedder()


@functools.lru_cache(maxsize=1)
def _local_embedder() -> LocalEmbedder:
    return LocalEmbedder()


def identity(embedder: Embedder) -> dict[str, Any]:
    return {"embedding_provider": embedder.provider, "embedding_model": f"{embedder.provider}:{embedder.model}",
            "embedding_dim": embedder.dim}


# ── Chroma ───────────────────────────────────────────────────

def store_path() -> Path:
    return get_settings().chroma_dir / COLLECTION


def _client(path: Path, create: bool) -> Any:
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
    except Exception as e:  # noqa: BLE001
        raise IndexMissingError("chromadb 를 불러오지 못함") from e
    if not create and not path.exists():
        raise IndexMissingError("색인 폴더 없음")
    try:
        path.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))
    except Exception as e:  # noqa: BLE001 (손상된 저장소, 권한 등)
        raise IndexMissingError("색인 저장소를 열지 못함") from e


def _exists(client: Any, name: str) -> bool:
    return name in {c.name for c in client.list_collections()}


def open_index(embedder: Embedder) -> tuple[Any, dict[str, Any]]:
    """검색용 컬렉션과 메타데이터. 없거나 비면 IndexMissingError, 식별 정보가 다르면 IndexMismatchError."""
    client = _client(store_path(), create=False)
    try:
        exists = _exists(client, COLLECTION)
        if exists:
            col = client.get_collection(COLLECTION, embedding_function=None)
            meta = dict(col.metadata or {})
            count = col.count()
    except Exception as e:  # noqa: BLE001
        raise IndexMissingError("컬렉션을 열지 못함") from e
    if not exists:
        raise IndexMissingError("컬렉션 없음")
    if count == 0:
        raise IndexMissingError("컬렉션이 비어 있음")
    try:
        current = identity(embedder)
    except Exception as e:  # noqa: BLE001
        raise IndexMissingError("임베딩 공급자를 준비하지 못함") from e
    check_identity(meta, current)
    return col, meta


def check_identity(meta: dict[str, Any], current: dict[str, Any]) -> None:
    stored_model = meta.get("embedding_model")
    if not stored_model:
        raise IndexMismatchError(f"색인에 임베딩 식별 정보가 없음. {REBUILD_HINT}")
    if stored_model != current["embedding_model"]:
        raise IndexMismatchError(
            f"색인 임베딩({stored_model})과 현재 설정({current['embedding_model']})이 다름. {REBUILD_HINT}")
    sd, cd = meta.get("embedding_dim"), current.get("embedding_dim")
    if sd and cd and int(sd) != int(cd):
        raise IndexMismatchError(f"임베딩 차원 불일치(색인 {sd}, 현재 {cd}). {REBUILD_HINT}")


def index_info(meta: dict[str, Any], count: int | None = None) -> dict[str, Any]:
    model = str(meta.get("embedding_model") or "")
    return {"provider": meta.get("embedding_provider") or model.split(":", 1)[0],
            "model": model.split(":", 1)[-1], "chunks": int(count if count is not None else meta.get("chunks") or 0),
            "docs": int(meta.get("docs") or 0), "built_at": meta.get("built_at")}


def chunk_docs(docs: list[dict[str, Any]]) -> list[Chunk]:
    out: list[Chunk] = []
    for i, d in enumerate(docs):
        out.extend(chunk_document(i, d.get("title") or "", d.get("text") or ""))
    return out


def _chunk_meta(c: Chunk, d: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_index": c.doc_index, "chunk_index": c.chunk_index, "doc_id": d.get("doc_id") or "",
        "title": d.get("title") or "", "source_url": d.get("source_url") or "",
        "license": d.get("license") or "", "section": c.section, "group": d.get("group") or "",
        "aliases": "|".join(d.get("aliases") or []), "catalog_ids": "|".join(d.get("catalog_ids") or []),
    }


def build_index(docs: list[dict[str, Any]], embedder: Embedder, *, batch: int = 64,
                log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """문서 → 청크 → 임베딩 → 임시 컬렉션 → 활성 컬렉션 교체. index_info 를 돌려준다."""
    chunks = chunk_docs(docs)
    if not chunks:
        raise ValueError("색인할 청크가 없습니다 (문서 JSON 이 비었음)")
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), batch):
        vectors.extend(embedder.embed([c.text for c in chunks[i:i + batch]]))
        log(f"  임베딩 {min(i + batch, len(chunks))}/{len(chunks)}")
    dim = len(vectors[0])
    ident = identity(embedder)
    if ident["embedding_dim"] and ident["embedding_dim"] != dim:
        raise IndexMismatchError(f"임베딩 차원이 예상({ident['embedding_dim']})과 다름({dim})")
    meta = {**ident, "embedding_dim": dim,
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "chunks": len(chunks), "docs": len(docs), "source": "ko.wikipedia.org (CC BY-SA 4.0)"}
    client = _client(store_path(), create=True)
    if _exists(client, STAGING):
        client.delete_collection(STAGING)
    col = client.create_collection(STAGING, embedding_function=None, metadata=meta,
                                   configuration={"hnsw": {"space": "cosine"}})
    for i in range(0, len(chunks), 500):
        part = chunks[i:i + 500]
        col.add(ids=[c.chunk_id for c in part], embeddings=vectors[i:i + 500],
                documents=[c.text for c in part], metadatas=[_chunk_meta(c, docs[c.doc_index]) for c in part])
    if col.count() != len(chunks):
        client.delete_collection(STAGING)
        raise RuntimeError("임시 컬렉션 건수 검증 실패")
    swap_in(client, col)
    doc_table.cache_clear()
    return index_info(meta, len(chunks))


def swap_in(client: Any, staging: Any) -> None:
    """검증한 임시 컬렉션을 활성 컬렉션으로 바꾼다. 실패하면 이전 활성 컬렉션을 되돌려 놓고 예외를 올린다."""
    if _exists(client, PREVIOUS):
        client.delete_collection(PREVIOUS)
    had_active = _exists(client, COLLECTION)
    if had_active:
        client.get_collection(COLLECTION, embedding_function=None).modify(name=PREVIOUS)
    try:
        staging.modify(name=COLLECTION)
    except Exception:
        if had_active and not _exists(client, COLLECTION):
            client.get_collection(PREVIOUS, embedding_function=None).modify(name=COLLECTION)
        raise
    if had_active:
        try:
            client.delete_collection(PREVIOUS)
        except Exception:  # noqa: BLE001 (남아도 다음 재구축 때 지운다)
            pass


@functools.lru_cache(maxsize=4)
def doc_table(path: str, built_at: str | None) -> dict[int, dict[str, Any]]:
    """문서 표 (doc_index → 제목, URL, 별칭, 카탈로그 ID). 색인 시각이 같으면 캐시를 쓴다."""
    client = _client(Path(path), create=False)
    try:
        col = client.get_collection(COLLECTION, embedding_function=None)
        got = col.get(where={"chunk_index": 0}, include=["metadatas"])
    except Exception as e:  # noqa: BLE001
        raise IndexMissingError("컬렉션을 열지 못함") from e
    table: dict[int, dict[str, Any]] = {}
    for m in got["metadatas"] or []:
        table[int(m["doc_index"])] = {
            "title": m.get("title") or "", "source_url": m.get("source_url") or "",
            "license": m.get("license") or "",
            "aliases": [a for a in str(m.get("aliases") or "").split("|") if a],
            "catalog_ids": [a for a in str(m.get("catalog_ids") or "").split("|") if a],
        }
    return table
