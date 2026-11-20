"""F3022 색인 만들기: data/space_docs.json → 청크 → 임베딩 → Chroma (settings.chroma_dir/space_docs).

    .venv/Scripts/python.exe -X utf8 backend/scripts/build_space_docs.py                  # EMBEDDING_PROVIDER (기본 local)
    .venv/Scripts/python.exe -X utf8 backend/scripts/build_space_docs.py --provider gms   # GMS 임베딩 API

local: chromadb 내장 ONNX all-MiniLM-L6-v2 (키 없음, 첫 실행에 모델을 내려받는다).
gms:   OpenAI 호환 임베딩 API, 모델 EMBEDDING_MODEL, 키 GMS_KEY (키는 출력하지 않는다).
검색할 때도 같은 공급자를 써야 한다 (다르면 Tool 이 INDEX_MISMATCH). 공급자를 바꾸면 .env 의 EMBEDDING_PROVIDER 도 바꾼다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from astrowatch.config import get_settings  # noqa: E402
from astrowatch.rag import store  # noqa: E402
from astrowatch.rag.collect import load_docs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=("local", "gms"), default=None, help="임베딩 공급자 (기본: EMBEDDING_PROVIDER)")
    args = ap.parse_args()

    s = get_settings()
    src = s.space_docs_file
    if not src.exists():
        print(f"문서 파일이 없습니다: {src}\n먼저 scripts/collect_space_docs.py 를 실행하세요.")
        return 1
    docs = [d for d in load_docs(src) if (d.get("text") or "").strip()]
    embedder = store.get_embedder(args.provider)
    print(f"문서 {len(docs)}개, 공급자 {embedder.provider}, 모델 {embedder.model}")
    t0 = time.perf_counter()
    try:
        info = store.build_index(docs, embedder, log=print)
    except store.IndexMissingError as e:
        print(f"색인을 만들지 못했습니다: {e}")
        return 1
    print(f"완료: 문서 {info['docs']}개, 청크 {info['chunks']}개, {info['provider']}:{info['model']}, "
          f"built_at {info['built_at']} ({time.perf_counter() - t0:.0f}s)")
    print(f"→ {store.store_path()}")
    if (args.provider or s.embedding_provider) != s.embedding_provider:
        print(f"주의: 현재 EMBEDDING_PROVIDER={s.embedding_provider}. 검색에 이 색인을 쓰려면 .env 를 맞추세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
