"""F3022 문서 수집: 한국어 위키백과 → settings.space_docs_file (기본 data/space_docs.json).

    .venv/Scripts/python.exe -X utf8 backend/scripts/collect_space_docs.py
    .venv/Scripts/python.exe -X utf8 backend/scripts/collect_space_docs.py --limit 20   # 앞 20개만 (점검용)

대상: 천체 카탈로그(astrowatch/data/sky_objects.json) 이름/별칭 + 용어집(astrowatch.rag.collect.GLOSSARY).
키가 필요 없다. 요청은 User-Agent 를 밝히고 1초 간격으로 하나씩 보낸다. 429 를 받으면 기다렸다 다시 시도하고, 받은 본문은 data/cache 에 남겨 다시 실행하면 이어받는다.
결과 JSON 은 저장소에 커밋한다 (본문 extract 만, 전체 약 2 MB 이하). 다음 단계: build_space_docs.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from astrowatch.astro.catalog import catalog  # noqa: E402
from astrowatch.config import get_settings  # noqa: E402
from astrowatch.rag import collect as C  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개 대상만 수집 (0=전체)")
    ap.add_argument("--max-bytes", type=int, default=C.MAX_BYTES, help="JSON 파일 크기 상한")
    ap.add_argument("--interval", type=float, default=C.REQUEST_INTERVAL, help="요청 간격(초)")
    args = ap.parse_args()

    settings = get_settings()
    out = settings.space_docs_file          # config 가 data/ 아래(또는 허용된 저장소 밖)로 제한한다
    specs = C.glossary_specs() + C.catalog_specs(catalog())
    if args.limit:
        specs = specs[: args.limit]
    print(f"수집 대상 {len(specs)}개 (용어 {len(C.GLOSSARY)} + 카탈로그), 요청 간격 {args.interval}s")
    t0 = time.perf_counter()
    client = C.WikiClient(interval=args.interval)
    cache_file = settings.space_docs_cache_file   # 재실행 시 이어받기 (gitignore). 결과 파일과 같은 폴더의 cache/
    docs, missing = C.collect(specs, client, cache_file=cache_file, log=lambda m: print(m, flush=True))
    if not docs:
        print("수집한 문서가 없어 저장하지 않습니다", file=sys.stderr)
        return 1
    return save(out, docs, missing, args.max_bytes, client.requests, t0)


def save(out: Path, docs: list, missing: list[str], max_bytes: int, requests: int = 0, t0: float | None = None) -> int:
    """실제 UTF-8 크기를 재서 예산 안에 맞춘 뒤에만 쓴다. 못 맞추면 쓰지 않고 2 를 돌려준다."""
    try:
        docs, cap, text = C.fit_to_budget(docs, max_bytes, missing=missing)
    except C.BudgetError as e:
        print(f"저장하지 않음: {e}", file=sys.stderr)
        return 2
    size = C.write_docs(out, docs, missing=missing, text=text)
    elapsed = time.perf_counter() - t0 if t0 is not None else 0.0
    print(f"문서 {len(docs)}개 저장 (못 찾음 {len(missing)}개, 요청 {requests}회, "
          f"문서당 상한 {cap}자, {size / 1024:.0f} KB, {elapsed:.0f}s)")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
