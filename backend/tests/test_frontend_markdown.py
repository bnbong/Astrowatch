"""frontend/js/markdown.js (R8) — node 로 직접 실행해 이스케이프 · 변환 규칙을 확인한다."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MARKDOWN_JS = Path(__file__).resolve().parents[2] / "frontend" / "js" / "markdown.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node 가 없어 프론트 렌더러 검사를 건너뜀")


def _render(text: str, fn: str = "render") -> str:
    script = (
        "const md = require(process.argv[1]);"
        "let s = '';"
        "process.stdin.on('data', (d) => (s += d));"
        f"process.stdin.on('end', () => process.stdout.write(JSON.stringify(md.{fn}(JSON.parse(s)))));"
    )
    out = subprocess.run(
        [NODE, "-e", script, str(MARKDOWN_JS)],
        input=json.dumps(text),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=True,
    )
    return json.loads(out.stdout)


def test_xss_is_escaped():
    html = _render("<img src=x onerror=alert(1)>")
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_bold_becomes_strong():
    assert _render("**a**") == "<p><strong>a</strong></p>"


def test_inline_code_and_bold_inside_code_untouched():
    html = _render("Tool `sky_forecast` 와 `**x**`")
    assert "<code>sky_forecast</code>" in html
    assert "<code>**x**</code>" in html


def test_lists_and_paragraphs():
    html = _render("요약\n줄2\n\n- 하나\n* 둘\n\n1. 첫째\n2. 둘째")
    assert html == (
        "<p>요약<br>줄2</p>"
        "<ul><li>하나</li><li>둘</li></ul>"
        "<ol><li>첫째</li><li>둘째</li></ol>"
    )


def test_links_and_tags_are_not_converted():
    html = _render("[x](javascript:alert(1)) ![i](http://e/x.png) <b>t</b> **<script>**")
    assert "<a" not in html and "<img" not in html and "<b>" not in html and "<script>" not in html
    assert "<strong>&lt;script&gt;</strong>" in html


def test_bold_line_is_not_a_list():
    assert _render("**조건부 충족** 입니다") == "<p><strong>조건부 충족</strong> 입니다</p>"


def test_render_inline_for_reasons():
    assert _render("달 영향 **없음** <i>", "renderInline") == "달 영향 <strong>없음</strong> &lt;i&gt;"
