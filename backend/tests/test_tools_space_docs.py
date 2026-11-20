"""심화 F3022 search_space_docs: 색인 없음/불일치/검색 결과/이름 가산. 네트워크, 키, 모델 내려받기 없음.

가짜 임베딩(글자 코드 해시 16차원)을 rag.store.set_embedder_override 로 끼우고,
conftest 가 CHROMA_DIR 을 임시 폴더로 바꿔 둔다.
"""

from __future__ import annotations

import pytest

from astrowatch.astro.catalog import find_object
from astrowatch.rag import store
from astrowatch.rag.chunking import chunk_document
from astrowatch.tools import space_docs
from astrowatch.tools.base import ErrorCode, ToolKind


class FakeEmbedder:
    provider = "fake"
    dim = 16

    def __init__(self, model: str = "hash16") -> None:
        self.model = model
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * 16
            for ch in t:
                if not ch.isspace():
                    v[ord(ch) % 16] += 1.0
            out.append(v)
        return out


M31_ID = find_object("안드로메다 은하")["id"]

DOCS = [
    {"doc_id": "wiki:1", "title": "은하", "source_url": "https://ko.wikipedia.org/wiki/%EC%9D%80%ED%95%98",
     "license": "CC BY-SA 4.0", "group": "glossary", "aliases": ["은하"], "catalog_ids": [],
     "text": "은하는 항성, 성간 물질, 암흑 물질이 중력으로 묶인 거대한 천체 집단이다.\n"
             "== 분류 ==\n은하는 모양에 따라 타원 은하, 나선 은하, 불규칙 은하로 나눈다. 어떤 천체야 라는 질문에 자주 나온다."},
    {"doc_id": "wiki:2", "title": "안드로메다 은하", "source_url": "https://ko.wikipedia.org/wiki/M31",
     "license": "CC BY-SA 4.0", "group": "messier", "aliases": ["안드로메다 은하", "Andromeda Galaxy", "m31"],
     "catalog_ids": [M31_ID],
     "text": "안드로메다 은하(M31)는 안드로메다자리 방향으로 약 250만 광년 떨어진 나선 은하이다.\n"
             "우리 은하와 함께 국부 은하군에 속하며 맨눈으로 볼 수 있는 가장 먼 천체 가운데 하나이다."},
    {"doc_id": "wiki:3", "title": "유성우", "source_url": "https://ko.wikipedia.org/wiki/%EC%9C%A0%EC%84%B1%EC%9A%B0",
     "license": "CC BY-SA 4.0", "group": "glossary", "aliases": ["유성우", "극대"], "catalog_ids": [],
     "text": "유성우는 혜성이 남긴 먼지 띠를 지구가 지날 때 많은 유성이 한 점(복사점)에서 퍼져 나오는 현상이다."},
    {"doc_id": "wiki:4", "title": "박명", "source_url": "https://ko.wikipedia.org/wiki/%EB%B0%95%EB%AA%85",
     "license": "CC BY-SA 4.0", "group": "glossary", "aliases": ["천문박명"], "catalog_ids": [],
     "text": "박명은 해가 진 뒤나 뜨기 전 하늘이 완전히 어둡지 않은 때이다. 천문박명은 해가 지평선 아래 18도까지이다."},
]


@pytest.fixture
def fake():
    emb = FakeEmbedder()
    store.set_embedder_override(emb)
    yield emb
    store.set_embedder_override(None)
    store.doc_table.cache_clear()


@pytest.fixture
def built(fake):
    info = store.build_index(DOCS, fake)
    return info


def call(query: str, k: int = 4):
    return space_docs.search_space_docs.invoke({"query": query, "k": k})


def test_spec_and_registration():
    from astrowatch.tools.registry import load_builtin_tools, registry

    load_builtin_tools()
    if registry.get("search_space_docs") is None:       # 다른 테스트가 레지스트리를 비웠으면 다시 등록
        registry.register(space_docs.search_space_docs)
    spec = registry.get("search_space_docs")
    assert spec.kind is ToolKind.LOCAL
    assert "배경 지식" in spec.description and "외부 API" in spec.description
    names = [t["name"] for t in registry.describe()]
    assert "search_space_docs" in names
    schema = spec.input_model.model_json_schema()["properties"]
    assert schema["query"]["maxLength"] == 300 and schema["k"]["maximum"] == 8 and schema["k"]["default"] == 4


def test_bad_input():
    assert call("").code is ErrorCode.BAD_INPUT
    assert call("x" * 301).code is ErrorCode.BAD_INPUT
    assert space_docs.search_space_docs.invoke({"query": "은하", "k": 9}).code is ErrorCode.BAD_INPUT


def test_no_index_when_absent(fake):
    r = call("안드로메다 은하는 어떤 천체야?")
    assert not r.ok and r.code is ErrorCode.NO_INDEX
    assert r.error.message == space_docs.NO_INDEX_MESSAGE
    assert "scripts/collect_space_docs.py" in r.error.message and "scripts/build_space_docs.py" in r.error.message
    assert fake.calls == 0                               # 색인이 없으면 임베딩도 부르지 않는다
    assert not store.store_path().exists()              # 검색이 빈 색인 폴더를 만들지 않는다


def test_no_index_when_collection_empty(fake):
    client = store._client(store.store_path(), create=True)
    client.create_collection(store.COLLECTION, embedding_function=None, metadata={"embedding_model": "fake:hash16"})
    assert call("은하").code is ErrorCode.NO_INDEX


def test_index_mismatch(built):
    store.set_embedder_override(FakeEmbedder(model="other-model"))
    r = call("은하")
    assert not r.ok and r.code is ErrorCode.INDEX_MISMATCH
    assert "다시 만드세요" in r.error.message
    assert not r.code.fatal and not r.code.retriable     # 키 문제가 아니다


def test_hits_have_title_source_snippet(built):
    r = call("유성우는 왜 생겨?")
    assert r.ok, r.error
    assert r.data.index.provider == "fake" and r.data.index.model == "hash16"
    assert r.data.index.chunks == built["chunks"] and r.data.index.docs == len(DOCS) and r.data.index.built_at
    top = r.data.hits[0]
    assert top.title == "유성우" and top.source_url.startswith("https://ko.wikipedia.org/wiki/")
    assert "복사점" in top.snippet and len(top.snippet) <= 400
    assert top.license == "CC BY-SA 4.0" and top.chunk_id.startswith("2:")
    assert len({h.title for h in r.data.hits}) == len(r.data.hits)   # 문서마다 하나 (중복 제거)
    assert "CC BY-SA" in (r.note or "")


@pytest.mark.parametrize("query", ["안드로메다 은하는 어떤 천체야?", "M31은 어떤 천체야?", "Andromeda Galaxy"])
def test_catalog_name_boost_puts_document_first(built, query):
    r = call(query, k=3)
    assert r.ok, r.error
    assert r.data.hits[0].title == "안드로메다 은하" and r.data.hits[0].name_match


def test_alias_boost_for_glossary_term(built):
    r = call("천문박명이 뭐야?")
    assert r.ok and r.data.hits[0].title == "박명"


def test_k_limits_hits(built):
    r = call("은하", k=1)
    assert r.ok and len(r.data.hits) == 1


def test_no_result_below_min_score(built, monkeypatch):
    from astrowatch.rag import search as S

    monkeypatch.setattr(S, "MIN_SCORE", 99.0)
    r = call("피자 레시피 알려줘")                        # 이름 일치 없음 + 최소 점수 미만
    assert not r.ok and r.code is ErrorCode.NO_RESULT


def test_chunk_ids_and_overlap():
    text = "\n".join(f"{i}번째 문단입니다. " + "가" * 180 for i in range(6))
    chunks = chunk_document(7, "제목", "== 개요 ==\n" + text)
    assert [c.chunk_id for c in chunks] == [f"7:{i}" for i in range(len(chunks))]
    assert all(len(c.body) <= 500 + 80 for c in chunks) and len(chunks) >= 3
    assert chunks[0].text.startswith("[제목 > 개요]\n")
    assert chunks[1].body[:20] in chunks[0].body                        # 앞 청크 끝을 겹쳐 시작


def test_rebuild_replaces_collection(built, fake):
    info = store.build_index(DOCS[:2], fake)
    assert info["docs"] == 2
    r = call("은하")
    assert r.ok and {h.title for h in r.data.hits} <= {"은하", "안드로메다 은하"}
    assert r.data.index.chunks == info["chunks"]


def test_name_match_survives_min_score(built, monkeypatch):
    from astrowatch.rag import search as S

    monkeypatch.setattr(S, "MIN_SCORE", 99.0)
    r = call("유성우는 왜 생겨?")
    assert r.ok and [h.title for h in r.data.hits] == ["유성우"]   # 이름이 맞은 문서만 근거로 남는다


# ── 리뷰 반영: 경로 제한(R4), 크기 예산(R5), 응답 상한(R6), 초기화 실패(R7), 안전한 교체 ──

def test_config_confines_rag_paths_to_data(monkeypatch, tmp_path):
    from astrowatch import config

    root = config.ROOT_DIR
    default_docs, default_chroma = root / "data/space_docs.json", root / "data/chroma"
    rejected = ["frontend/x.json", "frontend/index.html", "backend/astrowatch/x.json", "README.md", "data",
                str(root / "frontend" / "x.json")]           # 저장소 안 절대 경로도 data/ 밖이면 거부 (플래그가 있어도)
    for raw in rejected:
        monkeypatch.setenv("SPACE_DOCS_FILE", raw)
        monkeypatch.setenv("CHROMA_DIR", raw.replace("x.json", "chroma"))
        config.reset_settings_cache()
        s = config.get_settings()
        assert s.space_docs_file == default_docs, raw
        assert s.chroma_dir == default_chroma, raw
    monkeypatch.setenv("SPACE_DOCS_FILE", "data/foo.json")
    monkeypatch.setenv("CHROMA_DIR", "data/chroma2")
    config.reset_settings_cache()
    s = config.get_settings()
    assert s.space_docs_file == root / "data/foo.json" and s.chroma_dir == root / "data/chroma2"
    assert s.space_docs_cache_file == root / "data/cache/space_docs_raw.json"
    monkeypatch.setenv("SPACE_DOCS_FILE", str(tmp_path / "docs.json"))   # 저장소 밖 + ALLOW_EXTERNAL_PATHS=1 (conftest)
    config.reset_settings_cache()
    assert config.get_settings().space_docs_file == (tmp_path / "docs.json").resolve()
    monkeypatch.setenv("ALLOW_EXTERNAL_PATHS", "0")
    config.reset_settings_cache()
    assert config.get_settings().space_docs_file == default_docs


def _fake_docs(n: int = 5, chars: int = 12000) -> list[dict]:
    body = ("유성우는 혜성이 남긴 먼지 띠를 지구가 지날 때 생긴다. " * 400)[:chars]
    return [{"doc_id": f"wiki:{i}", "title": f"문서{i}", "source_url": f"https://ko.wikipedia.org/wiki/D{i}",
             "license": "CC BY-SA 4.0", "retrieved_at": "", "group": "glossary", "aliases": [], "catalog_ids": [],
             "text": body} for i in range(n)]


def test_fit_to_budget_measures_serialized_bytes(tmp_path):
    from astrowatch.rag import collect as C

    docs = _fake_docs()
    budget = len(C.render_docs(docs).encode("utf-8")) // 3
    capped, cap, text = C.fit_to_budget(docs, budget)
    assert len(text.encode("utf-8")) <= budget
    assert C.MIN_CHARS <= cap < C.MAX_CHARS and all(len(d["text"]) <= cap for d in capped)
    assert len(docs[0]["text"]) == 12000                         # 원본은 바꾸지 않는다
    out = tmp_path / "docs.json"
    size = C.write_docs(out, capped, text=text)
    assert out.stat().st_size == size <= budget                  # 잰 크기와 실제 파일 크기가 같다 (CRLF 변환 없음)


def test_budget_impossible_exits_nonzero_without_writing(tmp_path, capsys):
    import importlib.util
    from pathlib import Path

    from astrowatch.rag import collect as C

    docs = _fake_docs()
    with pytest.raises(C.BudgetError):
        C.fit_to_budget(docs, 2000)
    path = Path(__file__).resolve().parents[1] / "scripts" / "collect_space_docs.py"
    spec = importlib.util.spec_from_file_location("collect_space_docs_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "docs.json"
    assert mod.save(out, docs, [], 2000) == 2 and not out.exists()
    assert "저장하지 않음" in capsys.readouterr().err
    assert mod.save(out, docs, [], 10_000_000) == 0 and out.exists()


class _FakeResp:
    def __init__(self, body: bytes, headers: dict | None = None) -> None:
        self.body, self.headers, self.status_code = body, headers or {}, 200
        self.read = 0
        self.closed = False

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, n: int):
        for i in range(0, len(self.body), n):
            self.read += len(self.body[i:i + n])
            yield self.body[i:i + n]

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, resp: _FakeResp) -> None:
        self.resp, self.headers, self.streams = resp, {}, []

    def get(self, url, params=None, timeout=None, stream=False):
        self.streams.append(stream)
        return self.resp


def _extract_body(text: str) -> bytes:
    import json

    return json.dumps({"query": {"pages": [{"pageid": 1, "extract": text}]}}, ensure_ascii=False).encode("utf-8")


def test_wiki_client_rejects_oversized_response():
    from astrowatch.rag import collect as C

    resp = _FakeResp(_extract_body("가" * 200_000))
    session = _FakeSession(resp)
    client = C.WikiClient(interval=0, session=session, sleep=lambda _s: None, max_response_bytes=64 * 1024)
    with pytest.raises(C.ResponseTooLarge):
        client.fetch(1)
    assert session.streams == [True] and resp.closed
    assert resp.read <= 2 * 64 * 1024                            # 상한을 넘자마자 읽기를 멈춘다
    declared = _FakeResp(b"{}", headers={"Content-Length": str(C.MAX_RESPONSE_BYTES + 1)})
    with pytest.raises(C.ResponseTooLarge):
        C.WikiClient(interval=0, session=_FakeSession(declared), sleep=lambda _s: None).fetch(1)
    assert declared.read == 0                                    # 선언 크기만 보고 거부


def test_wiki_client_caps_extract_before_cache():
    from astrowatch.rag import collect as C

    long_text = "\n".join(f"{i}번째 문단입니다. " + "나" * 300 for i in range(200))   # 6만 자 이상
    client = C.WikiClient(interval=0, session=_FakeSession(_FakeResp(_extract_body(long_text))),
                          sleep=lambda _s: None)
    text = client.fetch(1)
    assert text and len(text) <= C.RAW_CAP_CHARS < len(long_text)
    intro = C.WikiClient(interval=0, session=_FakeSession(_FakeResp(_extract_body(long_text))),
                         sleep=lambda _s: None).fetch_intros([1])
    assert len(intro[1]) <= C.RAW_CAP_CHARS


def test_embedder_factory_failure_is_no_index(built, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("onnxruntime 없음")

    monkeypatch.setattr(store, "get_embedder", boom)
    r = call("은하")
    assert not r.ok and r.code is ErrorCode.NO_INDEX and r.error.message == space_docs.UNAVAILABLE_MESSAGE


class _FakeLocal(FakeEmbedder):
    provider = "local"
    dim = store.LOCAL_DIM

    def __init__(self) -> None:
        super().__init__(model=store.LOCAL_MODEL)

    def embed(self, texts):
        return [(v * 24) for v in super().embed(texts)]          # 16 x 24 = 384 차원


def test_local_embedding_function_construction_failure_is_no_index(monkeypatch):
    import chromadb.utils.embedding_functions as ef

    store.build_index(DOCS, _FakeLocal())

    class Broken:
        def __init__(self, *_a, **_k):
            raise RuntimeError("모델 내려받기 실패")

    monkeypatch.setattr(ef, "DefaultEmbeddingFunction", Broken)
    store._local_embedder.cache_clear()
    try:
        r = call("안드로메다 은하는 어떤 천체야?")
    finally:
        store._local_embedder.cache_clear()
        store.doc_table.cache_clear()
    assert not r.ok and r.code is ErrorCode.NO_INDEX and r.error.message == space_docs.UNAVAILABLE_MESSAGE


def test_chroma_client_failure_is_no_index(built, monkeypatch):
    import chromadb

    def broken(*_a, **_k):
        raise RuntimeError("손상된 sqlite")

    monkeypatch.setattr(chromadb, "PersistentClient", broken)
    r = call("은하")
    assert not r.ok and r.code is ErrorCode.NO_INDEX and r.error.message == space_docs.UNAVAILABLE_MESSAGE


def test_mismatch_still_separate_from_no_index(built):
    store.set_embedder_override(FakeEmbedder(model="other"))
    assert call("은하").code is ErrorCode.INDEX_MISMATCH


class _FCol:
    def __init__(self, client, name, metadata=None) -> None:
        self.client, self.name, self.metadata, self.items = client, name, metadata or {}, []

    def add(self, ids, **_kw) -> None:
        self.items.extend(ids)

    def count(self) -> int:
        return len(self.items)

    def modify(self, name: str) -> None:
        if self.client.fail_rename == (self.name, name):
            raise RuntimeError("이름 바꾸기 실패")
        del self.client.cols[self.name]
        self.name = name
        self.client.cols[name] = self


class _FClient:
    def __init__(self) -> None:
        self.cols: dict[str, _FCol] = {}
        self.fail_rename: tuple[str, str] | None = None

    def list_collections(self):
        return list(self.cols.values())

    def get_collection(self, name, embedding_function=None):
        return self.cols[name]

    def create_collection(self, name, embedding_function=None, metadata=None, configuration=None):
        col = _FCol(self, name, metadata)
        self.cols[name] = col
        return col

    def delete_collection(self, name) -> None:
        del self.cols[name]


@pytest.mark.parametrize("fail", [None, (store.STAGING, store.COLLECTION), (store.COLLECTION, store.PREVIOUS)])
def test_rebuild_swap_keeps_old_index_on_failure(fake, monkeypatch, fail):
    client = _FClient()
    old = client.create_collection(store.COLLECTION)
    old.items = ["old:0"]
    client.fail_rename = fail
    monkeypatch.setattr(store, "_client", lambda _path, create: client)
    if fail is None:
        info = store.build_index(DOCS, fake)
        assert set(client.cols) == {store.COLLECTION}
        assert client.cols[store.COLLECTION] is not old and client.cols[store.COLLECTION].count() == info["chunks"]
        return
    with pytest.raises(RuntimeError, match="이름 바꾸기 실패"):
        store.build_index(DOCS, fake)
    assert client.cols[store.COLLECTION] is old and old.items == ["old:0"]   # 이전 색인이 그대로 활성
    assert store.PREVIOUS not in client.cols
