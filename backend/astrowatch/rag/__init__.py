"""심화 F3022 RAG: 천문 배경 지식 문서 수집, 청킹, 색인(Chroma), 검색.

    collect.py   한국어 위키백과 본문(extract) 수집 → data/space_docs.json
    chunking.py  문단 기준 청크 (~500자, 겹침)
    store.py     Chroma 컬렉션 열기/만들기, 임베딩 공급자(local | gms), 임베딩 식별 정보 검사
    search.py    하이브리드 검색 (카탈로그/제목 일치 가산 + 벡터 유사도)

구조와 임베딩 식별 검사는 VastHorizon 2회차에서 이식했다.
"""
