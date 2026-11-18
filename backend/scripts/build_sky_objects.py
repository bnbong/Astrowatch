"""VastHorizon items.json → astrowatch/data/sky_objects.json (천체 카탈로그).

    python scripts/build_sky_objects.py [items.json 경로]   # 경로가 없으면 GitHub 원본을 내려받는다

대상: messier · star · constellation · milky_way 중 적경·적위가 있는 항목 (human_made · solar_system 제외).
생성된 JSON 은 저장소에 커밋한다 — 실행 시 네트워크가 필요 없다.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

SOURCE_URL = "https://raw.githubusercontent.com/bnbong/VastHorizon/main/data/items.json"
OUT = Path(__file__).resolve().parents[1] / "astrowatch" / "data" / "sky_objects.json"
GROUPS = ("messier", "star", "constellation", "milky_way")

# 별칭 보충 (원본에 없는 흔한 호칭)
EXTRA_ALIASES = {
    "M31": ["안드로메다", "안드로메다대은하"],
    "M45": ["좀생이별", "좀생이", "묘성"],
    "M42": ["오리온대성운"],
    "M13": ["헤라클레스대성단"],
    "Q321": ["은하수", "milkyway"],
    "Q12980": ["북극성", "northstar"],
}


def norm(text: str) -> str:
    """매칭용 정규화: 공백 제거 · 소문자."""
    return "".join(str(text).split()).lower()


def kind_of(item: dict[str, Any]) -> str:
    """분류 문자열 → galaxy | nebula | cluster | star | constellation | milky_way | dso (규칙 순서가 의미를 가진다)."""
    group = item.get("group")
    if group in ("star", "constellation", "milky_way"):
        return group
    text = f"{(item.get('attrs') or {}).get('분류') or ''} {item.get('kind') or ''} {item.get('name') or ''}"
    if "은하" in text:
        return "galaxy"
    if any(w in text for w in ("성단", "성군", "성협")):
        return "cluster"
    if any(w in text for w in ("성운", "전리수소영역", "초신성잔해")):
        return "nebula"
    if "이중성" in text:
        return "star"
    return "dso"


def aliases_of(item: dict[str, Any]) -> list[str]:
    attrs = item.get("attrs") or {}
    out: list[str] = []
    for v in (item.get("name"), item.get("name_en"), attrs.get("메시에 번호"), attrs.get("바이어 명칭"),
              attrs.get("라틴어 이름"), attrs.get("히파르코스 번호")):
        if v:
            out.append(norm(v))
    m = attrs.get("메시에 번호")
    if m:
        num = re.sub(r"\D", "", m)
        out += [f"메시에{num}", f"messier{num}"]
    out += [norm(a) for a in EXTRA_ALIASES.get(m or item.get("id") or "", [])]
    return list(dict.fromkeys(a for a in out if a))


def convert(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        if it.get("group") not in GROUPS or it.get("ra_deg") is None or it.get("dec_deg") is None:
            continue
        attrs = it.get("attrs") or {}
        mag = attrs.get("겉보기 등급")
        naked = attrs.get("맨눈 관측", attrs.get("맨눈 가능"))
        con = attrs.get("별자리") or (it.get("name") if it.get("group") == "constellation" else None)
        out.append({
            "id": attrs.get("메시에 번호") or it.get("id"),
            "group": it.get("group"),
            "name_ko": it.get("name"),
            "name_en": it.get("name_en"),
            "aliases": aliases_of(it),
            "kind": kind_of(it),
            "ra_deg": round(float(it["ra_deg"]), 5),
            "dec_deg": round(float(it["dec_deg"]), 5),
            "mag": float(mag) if isinstance(mag, (int, float)) else None,
            "naked_eye": naked if isinstance(naked, bool) else None,
            "constellation_ko": con,
        })
    return out


def load(path: str | None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    with urllib.request.urlopen(SOURCE_URL, timeout=30) as resp:  # noqa: S310 — 고정 URL
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str]) -> int:
    raw = load(argv[1] if len(argv) > 1 else None)
    items = raw["items"] if isinstance(raw, dict) else raw
    objs = convert(items)
    OUT.write_text(json.dumps({"source": SOURCE_URL, "count": len(objs), "objects": objs},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(objs)}개 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
