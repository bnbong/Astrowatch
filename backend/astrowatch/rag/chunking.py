"""문단 기준 청킹 (F3022). VastHorizon 2회차 chunking.py 에서 이식 (절 머리말 아이디어), 의존성 없이 단순화.

| 단계 | 규칙 |
|------|------|
| 1 | 본문을 줄 단위 문단으로 나눈다. '== 절 ==' 줄은 문단이 아니라 현재 절 이름이 된다 |
| 2 | 문단을 이어 붙여 CHUNK_SIZE(~500자) 이하로 묶는다. 넘치면 새 청크 |
| 3 | 한 문단이 CHUNK_SIZE 보다 길면 문장 경계('다. ', '. ')에서, 그래도 길면 글자 수로 자른다 |
| 4 | 새 청크는 앞 청크의 끝 OVERLAP(~80자)을 앞에 붙여 시작한다 (문맥 연결) |
| 5 | 임베딩용 텍스트는 머리말 '[문서 제목 > 절]' + 본문. 근거 표시(snippet)에는 본문만 쓴다 |

청크 ID 는 'doc_index:chunk_index' (doc_index 는 space_docs.json 의 문서 순서).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNK_SIZE = 500
OVERLAP = 80
_HEADING = re.compile(r"^(=+)\s*(.+?)\s*\1\s*$")
_SENT = re.compile(r"(?<=다\.)\s+|(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str        # 'doc_index:chunk_index'
    doc_index: int
    chunk_index: int
    section: str
    body: str            # 본문만 (snippet)
    text: str            # 머리말 + 본문 (임베딩)


def _pieces(paragraph: str, size: int) -> list[str]:
    """긴 문단을 문장 경계, 그다음 글자 수로 size 이하 조각으로 나눈다."""
    if len(paragraph) <= size:
        return [paragraph]
    out: list[str] = []
    cur = ""
    for sent in _SENT.split(paragraph):
        while len(sent) > size:                 # 문장 하나가 너무 길면 글자 수로
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:size])
            sent = sent[size:]
        if cur and len(cur) + 1 + len(sent) > size:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def _tail(text: str, n: int) -> str:
    """앞 청크의 끝 n 자. 가능하면 단어 경계에서 시작한다."""
    if len(text) <= n:
        return text
    tail = text[-n:]
    i = tail.find(" ")
    return tail[i + 1:] if 0 <= i < n // 2 else tail


def chunk_document(doc_index: int, title: str, text: str, *, size: int = CHUNK_SIZE,
                   overlap: int = OVERLAP) -> list[Chunk]:
    blocks: list[tuple[str, str]] = []          # (절, 문단 조각)
    section = ""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(2)
            continue
        for piece in _pieces(line, size):
            blocks.append((section, piece))

    bodies: list[tuple[str, str]] = []          # (청크 시작 절, 본문)
    cur, cur_section, prev = "", "", ""
    for sec, piece in blocks:
        if cur and len(cur) + 1 + len(piece) > size:
            bodies.append((cur_section, cur))
            prev = cur
            cur = ""
        if not cur:
            cur_section = sec
            lead = _tail(prev, overlap) if prev and overlap else ""
            cur = f"{lead} {piece}".strip() if lead and len(lead) + 1 + len(piece) <= size + overlap else piece
        else:
            cur = f"{cur}\n{piece}"
    if cur:
        bodies.append((cur_section, cur))

    chunks = []
    for i, (sec, body) in enumerate(bodies):
        header = f"[{title}{' > ' + sec if sec else ''}]"
        chunks.append(Chunk(chunk_id=f"{doc_index}:{i}", doc_index=doc_index, chunk_index=i, section=sec,
                            body=body, text=f"{header}\n{body}"))
    return chunks
