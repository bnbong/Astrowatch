"""한국어 위키백과 본문 수집 (F3022). VastHorizon 2회차에서 이식 (wikipedia.py, collect_docs.py).

수집 대상
| 묶음      | 출처                                   | 검색 제목                                  |
|-----------|----------------------------------------|--------------------------------------------|
| 카탈로그  | astrowatch/data/sky_objects.json 294개 | name_ko → 메시에 번호(M31) → name_en 순    |
| 용어      | GLOSSARY (이 모듈)                     | 용어마다 후보 제목 순 (동음이의 문서 회피) |

API: MediaWiki action=query, prop=extracts, explaintext (평문 본문). 리다이렉트를 따라간다.
예절: User-Agent 에 프로젝트 이름을 밝히고, 요청은 한 번에 하나씩 REQUEST_INTERVAL(1초) 간격으로 보내고, 도입부만 쓰는 문서는 20개씩 묶어 받는다.
maxlag=5 를 붙이고 429, maxlag, ratelimited 는 Retry-After(없으면 15~120초)만큼 기다려 다시 시도한다.
없는 문서, 동음이의 문서, 본문이 빈 문서는 건너뛴다.

저장 형식 (data/space_docs.json, 저장소에 커밋)
    {"source": ..., "license": "CC BY-SA 4.0", "collected_at": ..., "count": N,
     "docs": [{"doc_id", "title", "source_url", "license", "retrieved_at", "group", "aliases",
               "catalog_ids", "text"}]}
본문은 위키백과 문서의 앞부분 extract 만 저장한다 (문서당 최대 MAX_CHARS 자, 전체 MAX_BYTES 이하).

자원 한도
| 대상            | 한도                                                                          |
|-----------------|-------------------------------------------------------------------------------|
| API 응답 하나   | MAX_RESPONSE_BYTES(2 MB). 넘으면 ResponseTooLarge (스트리밍으로 읽다가 끊는다) |
| 원문 캐시 본문  | 문서당 RAW_CAP_CHARS(MAX_CHARS 의 2배)로 잘라 저장한다                         |
| JSON 파일       | fit_to_budget 이 실제 UTF-8 크기를 재며 문서당 상한을 줄인다. 못 맞추면 BudgetError |
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

API = "https://ko.wikipedia.org/w/api.php"
USER_AGENT = "Astrowatch/0.1 (SSAFY project; educational astronomy planner)"
LICENSE = "CC BY-SA 4.0"
SOURCE = "한국어 위키백과 (ko.wikipedia.org), MediaWiki API extracts"
REQUEST_INTERVAL = 1.0      # 초. 요청 사이 최소 간격
MAX_CHARS = 6000            # 문서당 본문 상한
MIN_CHARS = 500             # 크기 예산을 맞출 때 문서당 본문 상한의 하한
RAW_CAP_CHARS = 2 * MAX_CHARS   # 원문 캐시에 남기는 문서당 본문 상한
MAX_BYTES = 1_900_000       # JSON 파일 전체 상한 (UTF-8). 넘으면 문서당 상한을 함께 줄인다
MAX_RESPONSE_BYTES = 2 * 1024 * 1024   # API 응답 하나의 상한 (clients/http.py 와 같은 값)
_READ_CHUNK = 64 * 1024
TRANSIENT_ERRORS = {"maxlag", "ratelimited", "readonly"}
MAX_RETRIES = 4
BACKOFF = (15, 30, 60, 120)  # 429 에 Retry-After 가 없을 때 기다릴 초

# 이 절부터 문서 끝까지는 근거로 쓰지 않는다
TAIL_SECTIONS = ("같이 보기", "각주", "내용주", "참고 문헌", "참고문헌", "외부 링크", "출처", "더 읽어보기")
_HEADING = re.compile(r"^(=+)\s*(.+?)\s*\1\s*$")

# 이 서비스 도메인의 용어: (대표 용어, 후보 제목들). 첫 번째로 찾은 문서를 쓴다.
GLOSSARY: list[tuple[str, list[str]]] = [
    ("유성우", ["유성우"]),
    ("복사점", ["복사점", "유성우"]),
    ("천문박명", ["박명", "천문박명"]),
    ("월령", ["월령 (천문학)", "달의 나이", "월령", "달의 위상"]),
    ("달의 위상", ["달의 위상"]),
    ("합", ["합 (천문학)"]),
    ("충", ["충 (천문학)"]),
    ("최대이각", ["이각 (천문학)", "최대 이각", "이각", "최대이각"]),
    ("극대", ["유성우"]),
    ("월식", ["월식"]),
    ("일식", ["일식"]),
    ("메시에 천체", ["메시에 천체", "메시에 목록"]),
    ("적경", ["적경"]),
    ("적위", ["적위"]),
    ("남중", ["남중", "자오선 통과"]),
    ("광해", ["광공해", "빛공해"]),
    ("성운", ["성운"]),
    ("성단", ["성단"]),
    ("산개 성단", ["산개 성단"]),
    ("구상 성단", ["구상 성단"]),
    ("은하", ["은하"]),
    ("행성", ["행성"]),
    ("혜성", ["혜성"]),
    ("소행성", ["소행성"]),
    ("국제우주정거장", ["국제 우주 정거장", "국제우주정거장"]),
    ("황도", ["황도"]),
    ("겉보기 등급", ["겉보기 등급"]),
    ("천구", ["천구"]),
    ("별자리", ["별자리"]),
]


class ResponseTooLarge(RuntimeError):
    """API 응답이 MAX_RESPONSE_BYTES 를 넘었다."""


class BudgetError(RuntimeError):
    """문서당 상한을 MIN_CHARS 까지 줄여도 JSON 파일이 크기 예산을 넘는다."""


def read_capped_json(resp: Any, limit: int = MAX_RESPONSE_BYTES) -> dict[str, Any]:
    """응답 본문을 limit 바이트까지만 읽어 JSON 으로 푼다. 넘으면 ResponseTooLarge (나머지는 받지 않는다)."""
    declared = str((resp.headers or {}).get("Content-Length", ""))
    if declared.isdigit() and int(declared) > limit:
        raise ResponseTooLarge(f"MediaWiki 응답이 너무 큼 ({int(declared)} 바이트 > {limit})")
    buf = bytearray()
    for chunk in resp.iter_content(_READ_CHUNK):
        buf.extend(chunk)
        if len(buf) > limit:
            raise ResponseTooLarge(f"MediaWiki 응답이 너무 큼 ({limit} 바이트 초과)")
    return json.loads(bytes(buf).decode("utf-8"))


@dataclass
class Spec:
    """수집할 문서 하나. candidates 를 순서대로 찾아 처음 찾은 문서를 쓴다."""

    key: str
    group: str                       # catalog 그룹(messier | star | constellation | milky_way) 또는 glossary
    candidates: list[str]
    aliases: list[str] = field(default_factory=list)
    catalog_id: str | None = None


def page_url(title: str) -> str:
    return "https://ko.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))


def clean_extract(text: str) -> str:
    """꼬리 절(각주 등)을 지우고 빈 줄을 정리한다. 절 제목(== 역사 ==)은 청킹 단서로 남긴다."""
    lines: list[str] = []
    for line in text.splitlines():
        heading = _HEADING.match(line.strip())
        if heading and heading.group(2) in TAIL_SECTIONS:
            break
        if line.strip():
            lines.append(line.strip())
    kept: list[str] = []
    for i, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading:
            nxt = _HEADING.match(lines[i + 1]) if i + 1 < len(lines) else None
            if i + 1 == len(lines) or (nxt and len(nxt.group(1)) <= len(heading.group(1))):
                continue                       # 내용 없는 절 제목
            kept.append("")
        kept.append(line)
    return "\n".join(kept).strip()


def cap_text(text: str, limit: int) -> str:
    """limit 자 이하로 자른다. 가능하면 문단/문장 경계에서 자른다."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("\n", "다. ", ". "):
        i = cut.rfind(sep)
        if i >= limit * 0.6:
            return cut[: i + len(sep)].strip()
    return cut.strip()


def catalog_specs(objects: list[dict[str, Any]]) -> list[Spec]:
    specs = []
    for o in objects:
        name_ko, name_en = o.get("name_ko") or "", o.get("name_en") or ""
        cands = [name_ko]
        for a in o.get("aliases") or []:
            m = re.fullmatch(r"m(\d{1,3})", a)
            if m:
                cands.append(f"M{m.group(1)}")
        if name_en:
            cands.append(name_en)
        aliases = [x for x in [name_ko, name_en, *(o.get("aliases") or [])] if x]
        specs.append(Spec(key=o["id"], group=o.get("group") or "catalog",
                          candidates=list(dict.fromkeys(c for c in cands if c)),
                          aliases=list(dict.fromkeys(aliases)), catalog_id=o["id"]))
    return specs


def glossary_specs() -> list[Spec]:
    return [Spec(key=f"term:{term}", group="glossary", candidates=titles, aliases=[term])
            for term, titles in GLOSSARY]


class WikiClient:
    """MediaWiki API 클라이언트. 요청 간격과 일시 오류 재시도를 지킨다."""

    def __init__(self, interval: float = REQUEST_INTERVAL, session: Any | None = None,
                 sleep: Callable[[float], None] = time.sleep, *, max_response_bytes: int = MAX_RESPONSE_BYTES,
                 raw_cap_chars: int = RAW_CAP_CHARS) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.interval = interval
        self.sleep = sleep
        self.max_response_bytes = max_response_bytes
        self.raw_cap_chars = raw_cap_chars
        self._last = 0.0
        self.requests = 0

        self.throttled = 0

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        """요청 하나. 429/maxlag/ratelimited 는 Retry-After(없으면 BACKOFF)만큼 기다려 다시 시도."""
        for attempt in range(MAX_RETRIES + 1):
            wait = self.interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            resp = self.session.get(API, params=params, timeout=30, stream=True)
            try:
                transient = resp.status_code == 429
                data: dict[str, Any] = {}
                if not transient:
                    resp.raise_for_status()
                    data = read_capped_json(resp, self.max_response_bytes)
                    transient = (data.get("error") or {}).get("code") in TRANSIENT_ERRORS
            finally:
                close = getattr(resp, "close", None)
                if close:
                    close()
            if transient:
                self.throttled += 1
                if attempt >= MAX_RETRIES:
                    raise RuntimeError("MediaWiki 요청 제한(429) 재시도 초과")
                self.sleep(_retry_after(resp, attempt))
                continue
            if data.get("error"):
                raise RuntimeError(f"MediaWiki 오류: {data['error'].get('code')}")
            return data
        raise RuntimeError("MediaWiki 재시도 초과")

    def resolve(self, titles: list[str]) -> dict[str, dict[str, Any] | None]:
        """제목 → {"pageid", "title"} (정규화/리다이렉트 반영). 없음/동음이의면 None. 50개씩 한 번에 묻는다."""
        out: dict[str, dict[str, Any] | None] = {}
        uniq = list(dict.fromkeys(t for t in titles if t))
        for i in range(0, len(uniq), 50):
            batch = uniq[i:i + 50]
            data = self._get({
                "action": "query", "format": "json", "formatversion": 2, "maxlag": 5,
                "titles": "|".join(batch), "redirects": 1, "prop": "pageprops", "ppprop": "disambiguation",
            })
            q = data.get("query", {})
            hop = {x["from"]: x["to"] for x in q.get("normalized", [])}
            redirect = {x["from"]: x["to"] for x in q.get("redirects", [])}
            pages = {p.get("title"): p for p in q.get("pages", [])}
            for t in batch:
                name = hop.get(t, t)
                name = redirect.get(name, name)
                page = pages.get(name)
                bad = (page is None or page.get("missing") or page.get("invalid")
                       or "disambiguation" in (page.get("pageprops") or {}))
                out[t] = None if bad else {"pageid": page["pageid"], "title": page["title"]}
        return out

    def fetch(self, pageid: int) -> str | None:
        """문서 전체 본문(평문 extract). 빈 본문이면 None."""
        data = self._get({
            "action": "query", "format": "json", "formatversion": 2, "maxlag": 5,
            "pageids": pageid, "prop": "extracts", "explaintext": 1,
        })
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        return cap_text(clean_extract(pages[0].get("extract") or ""), self.raw_cap_chars) or None

    def fetch_intros(self, pageids: list[int]) -> dict[int, str]:
        """문서 첫 절(도입부)만 20개씩 한 번에 받는다 (exintro, 요청 수를 줄인다)."""
        out: dict[int, str] = {}
        for i in range(0, len(pageids), 20):
            batch = pageids[i:i + 20]
            data = self._get({
                "action": "query", "format": "json", "formatversion": 2, "maxlag": 5,
                "pageids": "|".join(map(str, batch)), "prop": "extracts", "explaintext": 1, "exintro": 1,
                "exlimit": 20,
            })
            for p in data.get("query", {}).get("pages", []):
                text = cap_text(clean_extract(p.get("extract") or ""), self.raw_cap_chars)
                if text and p.get("pageid"):
                    out[int(p["pageid"])] = text
        return out


def _retry_after(resp: Any, attempt: int = 0) -> float:
    raw = str((resp.headers or {}).get("Retry-After", ""))
    return float(raw) if raw.isdigit() else float(BACKOFF[min(attempt, len(BACKOFF) - 1)])


def full_text_wanted(doc: dict[str, Any]) -> bool:
    """전체 본문을 받을 문서: 용어, 은하수, 고유 이름이 있는 메시에 천체. 나머지(번호뿐인 메시에, 별, 별자리)는 도입부만."""
    if doc["group"] in ("glossary", "milky_way"):
        return True
    first = doc["aliases"][0] if doc["aliases"] else ""
    return doc["group"] == "messier" and not first.startswith("메시에 ")


def collect(specs: list[Spec], client: WikiClient | None = None, *, max_chars: int = MAX_CHARS,
            cache_file: Path | None = None,
            log: Callable[[str], None] = print) -> tuple[list[dict[str, Any]], list[str]]:
    """(문서 목록, 찾지 못한 key 목록).

    1) 모든 후보 제목의 존재 여부를 50개씩 묶어 확인한다 (없는 제목에는 본문 요청을 보내지 않는다).
    2) 대상마다 처음 찾은 문서를 고르고, 같은 문서(pageid)는 하나로 합쳐 별칭을 모은다.
    3) 용어/주요 천체는 전체 본문을 하나씩, 나머지는 도입부를 20개씩 받는다.
    cache_file 을 주면 받은 본문을 그때그때 저장하고, 다시 실행할 때 이미 받은 문서는 건너뛴다.
    """
    client = client or WikiClient()
    cache: dict[str, str] = {}
    if cache_file and cache_file.exists():
        cache = json.loads(cache_file.read_text(encoding="utf-8"))

    def save() -> None:
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    resolved = client.resolve([t for s in specs for t in s.candidates])
    log(f"  제목 확인 {len(resolved)}개 (요청 {client.requests}회)")
    by_page: dict[int, dict[str, Any]] = {}
    missing: list[str] = []
    for spec in specs:
        hit = next((resolved[t] for t in spec.candidates if resolved.get(t)), None)
        if hit is None:
            missing.append(spec.key)
            continue
        doc = by_page.setdefault(hit["pageid"], {
            "doc_id": f"wiki:{hit['pageid']}", "title": hit["title"], "source_url": page_url(hit["title"]),
            "license": LICENSE, "retrieved_at": "", "group": spec.group, "aliases": [], "catalog_ids": [],
            "text": "",
        })
        doc["aliases"] = list(dict.fromkeys([*doc["aliases"], *spec.aliases]))
        if spec.catalog_id and spec.catalog_id not in doc["catalog_ids"]:
            doc["catalog_ids"].append(spec.catalog_id)

    full = [pid for pid, d in by_page.items() if full_text_wanted(d) and str(pid) not in cache]
    intro = [pid for pid, d in by_page.items() if not full_text_wanted(d) and str(pid) not in cache]
    log(f"  문서 {len(by_page)}개: 전체 본문 {len(full)}개, 도입부 {len(intro)}개 받을 차례 (캐시 {len(cache)}개)")
    try:
        for i in range(0, len(intro), 20):
            cache.update({str(k): v for k, v in client.fetch_intros(intro[i:i + 20]).items()})
            save()
        for i, pid in enumerate(full, 1):
            try:
                text = client.fetch(pid)
            except Exception as e:  # noqa: BLE001 (한 문서 실패로 전체를 멈추지 않는다)
                log(f"  ! {by_page[pid]['title']}: {e}")
                text = None
            if text:
                cache[str(pid)] = text
                save()
            if i % 10 == 0:
                log(f"  본문 {i}/{len(full)} (요청 {client.requests}회, 제한 응답 {client.throttled}회)")
    finally:
        save()
    docs: list[dict[str, Any]] = []
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for pid, doc in by_page.items():
        text = cache.get(str(pid))
        if not text:
            continue
        doc["text"] = cap_text(text, max_chars)
        doc["retrieved_at"] = now
        docs.append(doc)
    return docs, missing


def render_docs(docs: list[dict[str, Any]], *, missing: list[str] | None = None) -> str:
    """저장할 JSON 문자열 (write_docs 가 이 문자열을 그대로 쓴다)."""
    payload = {
        "source": SOURCE,
        "license": LICENSE,
        "license_note": "위키백과 문서 본문의 앞부분(extract)만 저장한다. 원문과 기여자는 source_url 에서 확인한다.",
        "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "count": len(docs),
        "missing": missing or [],
        "docs": docs,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def fit_to_budget(docs: list[dict[str, Any]], max_bytes: int = MAX_BYTES, *, max_chars: int = MAX_CHARS,
                  min_chars: int = MIN_CHARS, missing: list[str] | None = None) -> tuple[list[dict[str, Any]], int, str]:
    """(상한을 건 문서, 적용한 문서당 상한, 저장할 JSON 문자열).

    실제로 직렬화한 UTF-8 크기를 잰다. max_bytes 를 넘으면 문서당 상한을 0.8 배씩 min_chars 까지 줄인다.
    min_chars 에서도 넘으면 BudgetError (아무것도 쓰지 않도록 호출자가 멈춘다). 원본 docs 는 바꾸지 않는다.
    """
    cap = max(min_chars, max_chars)
    while True:
        capped = [{**d, "text": cap_text(d.get("text") or "", cap)} for d in docs]
        text = render_docs(capped, missing=missing)
        size = len(text.encode("utf-8"))
        if size <= max_bytes:
            return capped, cap, text
        if cap <= min_chars:
            raise BudgetError(f"문서당 본문을 {min_chars}자로 줄여도 {size} 바이트로 상한 {max_bytes} 바이트를 넘습니다. "
                              "--max-bytes 를 늘리거나 --limit 로 대상을 줄이세요")
        cap = max(min_chars, int(cap * 0.8))


def write_docs(path: Path, docs: list[dict[str, Any]], *, missing: list[str] | None = None,
               text: str | None = None) -> int:
    """JSON 을 쓰고 바이트 수를 돌려준다. text 를 주면(fit_to_budget 결과) 잰 그대로 쓴다."""
    text = text if text is not None else render_docs(docs, missing=missing)
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)          # write_text 는 Windows 에서 줄바꿈을 CRLF 로 바꿔 잰 크기와 달라진다
    return len(data)


def load_docs(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("docs") or [])
