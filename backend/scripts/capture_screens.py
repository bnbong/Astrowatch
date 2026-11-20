r"""화면 캡처 · 데모 녹화 (Playwright + 설치된 Edge).

    cd backend
    ..\.venv\Scripts\python -X utf8 scripts\capture_screens.py [--base http://127.0.0.1:8000] [--no-video]

전제: 서버가 떠 있고(기본 8000), LLM 키가 설정돼 있다. 선택: 8001(카카오 키 없음) · 8002(LLM 키 없음)
서버가 떠 있으면 Tool 실패 · 키 누락 안내 장면도 찍는다. 결과: docs/screenshots/*.png, docs/demo/*.webm
실제 LLM 호출이 일어난다(약 10회). 캘린더 등록 버튼은 누르지 않는다(실제 일정 생성 방지).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
DEMO = ROOT / "docs" / "demo"
R1 = "12월 14일 쌍둥이자리 유성우, 서울에서 밤 10시부터 새벽 2시 사이에 볼 수 있어?"
R6 = "같은 날 장소만 대전으로 바꿔 줘"
R3 = "다음 주 아무 날이나 서울에서 보름달 피해서 유성 보고 싶어"
BAD_DATE = "2026년 2월 30일 서울에서 밤 9시부터 11시까지 별을 보고 싶어."
ANDRO_1 = "안드로메다 은하를 제일 잘 볼 수 있는 날짜를 추천해줘"
ANDRO_2 = "대전 유성구 지역에서. 날짜는 아직 미정이라 보기 좋은 날짜를 추천해줘"
R4 = "토요일 밤 9시부터 대전 근처 천문대에서 보고 싶어, 20km 이내. 자정 전에는 끝내야 해"


def wait_fonts(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts && document.fonts.ready")
    page.wait_for_timeout(300)


def send(page: Page, text: str, timeout_ms: int = 120_000, slow: bool = False) -> None:
    """메시지를 보내고 응답(처리 중 표시가 사라질 때)까지 기다린다."""
    box = page.locator("#chat-input")
    box.click()
    if slow:
        box.type(text, delay=35)
    else:
        box.fill(text)
    before = page.locator("#messages .msg-user, #messages .msg-bot").count()
    page.locator("#send-btn").click()
    # 빠른 응답(LLM 키 없음 등)은 '처리 중' 표시가 금방 사라지므로 말풍선 수 증가만 기준으로 기다린다
    page.wait_for_function(f"document.querySelectorAll('#messages .msg-user, #messages .msg-bot').length > {before + 1}", timeout=timeout_ms)
    try:
        page.locator("#status").wait_for(state="hidden", timeout=5_000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(600)


def shot(page: Page, name: str, full: bool = False) -> None:
    wait_fonts(page)
    page.screenshot(path=str(SHOTS / name), full_page=full)
    print("  shot", name)


def limit_scene(page: Page, mock_base: str) -> None:
    """모의 예보(모든 시간 흐림) 서버 — 재계획 3회 후 상한 도달 배너와 타임라인 재계획 구분선."""
    page.goto(f"{mock_base}/", wait_until="networkidle")
    page.locator("#new-chat").click()
    send(page, R3, timeout_ms=240_000)
    shot(page, "07_replan_and_stop_reason.png")
    panel = page.locator("#exec-panel")
    marker = page.locator("#exec-panel .tl-tag.is-replan").first
    if marker.count():
        marker.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
    panel.screenshot(path=str(SHOTS / "08_timeline_replan_marker.png"))
    print("  shot 08_timeline_replan_marker.png")


def chat_session(page: Page, base: str) -> None:
    page.goto(f"{base}/", wait_until="networkidle")
    page.locator("#new-chat").click()
    page.wait_for_timeout(300)
    shot(page, "01_chat_empty.png")

    send(page, R1)
    shot(page, "02_chat_r1_recommendation.png")
    page.locator("#messages .card").last.scroll_into_view_if_needed()
    page.locator("#messages .card").last.screenshot(path=str(SHOTS / "03_card_timebar.png"))
    page.locator("#exec-panel").screenshot(path=str(SHOTS / "04_execution_timeline.png"))
    print("  shot 03_card_timebar.png / 04_execution_timeline.png")

    page.locator("#messages .save-btn").last.click()
    page.locator("#messages .save-msg.ok").last.wait_for(timeout=15_000)
    page.wait_for_timeout(400)
    shot(page, "05_plan_saved.png")

    send(page, R6)
    shot(page, "06_followup_context_kept.png")

    # 재계획 (실제 예보 기준 — 재계획 구분선이 타임라인에 보인다)
    page.locator("#new-chat").click()
    send(page, R3, timeout_ms=180_000)
    shot(page, "07_replan_and_stop_reason.png")
    page.locator("#exec-panel").screenshot(path=str(SHOTS / "08_timeline_replan_marker.png"))

    page.locator("#new-chat").click()
    send(page, BAD_DATE)
    shot(page, "09_clarify_invalid_date.png")

    page.locator("#new-chat").click()
    send(page, ANDRO_1)
    send(page, ANDRO_2, timeout_ms=180_000)
    shot(page, "10_andromeda_deep_sky.png")
    page.locator("#messages .card").first.scroll_into_view_if_needed()
    page.locator("#messages .card").first.screenshot(path=str(SHOTS / "11_timebar_lanes_moon_weather.png"))

    # 세션 복원: 새 대화 → 최근 대화 목록에서 첫 항목 클릭
    page.locator("#new-chat").click()
    page.wait_for_timeout(300)
    page.locator("#convo-list .convo-item").first.locator(".convo-row").click()
    page.wait_for_function("document.querySelectorAll('#messages .msg-user, #messages .msg-bot').length >= 2", timeout=15_000)
    page.wait_for_timeout(500)
    shot(page, "12_session_restored.png")


def plans_and_logs(page: Page, base: str) -> None:
    page.goto(f"{base}/plans.html", wait_until="networkidle")
    page.wait_for_timeout(500)
    shot(page, "13_plans_list.png")
    rows = page.locator(".plan-row")
    if rows.count():
        rows.first.click()
        page.locator("#plan-detail").wait_for(state="visible", timeout=10_000)
        page.wait_for_timeout(400)
        shot(page, "14_plan_detail_drawer.png")

    page.goto(f"{base}/logs.html", wait_until="networkidle")
    page.wait_for_timeout(500)
    shot(page, "15_logs_sessions.png")
    cards = page.locator(".session-card")
    if cards.count():
        cards.first.click()
        page.locator("#session-detail table").wait_for(timeout=15_000)
        page.wait_for_timeout(400)
        shot(page, "16_log_events_table.png")

    page.goto(f"{base}/docs", wait_until="networkidle")
    page.wait_for_timeout(1500)
    shot(page, "17_fastapi_swagger.png", full=True)


def failure_scenes(ctx, base_nokakao: str | None, base_nollm: str | None) -> None:
    if base_nokakao:
        p = ctx.new_page()
        try:
            p.goto(f"{base_nokakao}/", wait_until="networkidle")
            p.locator("#new-chat").click()
            send(p, R4, timeout_ms=180_000)
            # 경고 스트립(카카오 키 누락 안내)이 보이도록 마지막 응답의 머리 부분으로 스크롤
            p.locator("#messages .msg-bot").last.scroll_into_view_if_needed()
            p.evaluate("document.querySelectorAll('#messages .msg-bot')[document.querySelectorAll('#messages .msg-bot').length-1].scrollIntoView({block:'start'})")
            p.wait_for_timeout(300)
            shot(p, "18_tool_failure_warning_kakao.png")
        except Exception as e:  # noqa: BLE001
            print("  (skip 18)", type(e).__name__)
        p.close()
    if base_nollm:
        p = ctx.new_page()
        try:
            p.goto(f"{base_nollm}/", wait_until="networkidle")
            p.locator("#new-chat").click()
            send(p, R1, timeout_ms=60_000)
            shot(p, "19_llm_key_missing_notice.png")
        except Exception as e:  # noqa: BLE001
            print("  (skip 19)", type(e).__name__)
        p.close()


def mobile(browser, base: str) -> None:
    ctx = browser.new_context(bypass_csp=True,viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    p = ctx.new_page()
    p.goto(f"{base}/", wait_until="networkidle")
    p.wait_for_timeout(400)
    # 최근 대화에서 복원해 카드가 있는 상태로
    try:
        p.locator("#convo-toggle").click()
        p.locator("#convo-list-m .convo-item").first.locator(".convo-row").click()
        p.wait_for_function("document.querySelectorAll('#messages .msg-user, #messages .msg-bot').length >= 2", timeout=15_000)
        p.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass
    shot(p, "20_mobile_chat.png")
    p.goto(f"{base}/plans.html", wait_until="networkidle")
    p.wait_for_timeout(400)
    shot(p, "21_mobile_plans.png")
    ctx.close()


def demo_video(browser, base: str) -> Path | None:
    """데모 흐름을 녹화한다: 질문 → 추천 카드 → 저장 → 후속 질문 → 계획 → 로그."""
    tmp = DEMO / "_rec"
    if tmp.exists():
        shutil.rmtree(tmp)
    ctx = browser.new_context(bypass_csp=True,viewport={"width": 1280, "height": 800}, record_video_dir=str(tmp),
                              record_video_size={"width": 1280, "height": 800})
    p = ctx.new_page()
    p.goto(f"{base}/", wait_until="networkidle")
    p.locator("#new-chat").click()
    p.wait_for_timeout(1200)
    send(p, R1, slow=True)
    p.wait_for_timeout(2500)
    p.locator("#messages .card").last.scroll_into_view_if_needed()
    p.wait_for_timeout(1500)
    p.locator("#messages .save-btn").last.click()
    p.locator("#messages .save-msg.ok").last.wait_for(timeout=15_000)
    p.wait_for_timeout(1500)
    send(p, R6, slow=True)
    p.wait_for_timeout(2500)
    p.locator("#tab-evidence").click()
    p.wait_for_timeout(1500)
    p.locator("#tab-timeline").click()
    p.wait_for_timeout(1000)
    p.goto(f"{base}/plans.html", wait_until="networkidle")
    p.wait_for_timeout(1200)
    rows = p.locator(".plan-row")
    if rows.count():
        rows.first.click()
        p.wait_for_timeout(2000)
    p.goto(f"{base}/logs.html", wait_until="networkidle")
    p.wait_for_timeout(1000)
    cards = p.locator(".session-card")
    if cards.count():
        cards.first.click()
        p.wait_for_timeout(2500)
    video = p.video
    ctx.close()
    if not video:
        return None
    src = Path(video.path())
    dst = DEMO / "astrowatch_demo.webm"
    shutil.move(str(src), str(dst))
    shutil.rmtree(tmp, ignore_errors=True)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--nokakao", default="http://127.0.0.1:8001")
    ap.add_argument("--nollm", default="http://127.0.0.1:8002")
    ap.add_argument("--mock", default="", help="WEATHER_PROVIDER=mock 서버 주소 — 상한 도달(limit_reached) 장면용")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--only", default="", help="chat,plans,fail,mobile,video,limit 중 쉼표 구분")
    a = ap.parse_args()
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    SHOTS.mkdir(parents=True, exist_ok=True)
    DEMO.mkdir(parents=True, exist_ok=True)
    for stale in SHOTS.glob("_probe*.png"):
        stale.unlink()

    import urllib.request

    def alive(base: str) -> bool:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=3) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    if not alive(a.base):
        print("서버가 응답하지 않습니다:", a.base)
        return 1
    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(bypass_csp=True,viewport={"width": 1400, "height": 900}, device_scale_factor=1.5)
        page = ctx.new_page()
        if not only or "chat" in only:
            print("[chat]"); chat_session(page, a.base)
        if not only or "plans" in only:
            print("[plans/logs/docs]"); plans_and_logs(page, a.base)
        if a.mock and ("limit" in only or not only):
            if alive(a.mock):
                print("[limit scene · mock weather]"); limit_scene(page, a.mock)
            else:
                print("mock 서버가 응답하지 않습니다:", a.mock)
        if not only or "fail" in only:
            print("[failure scenes]")
            failure_scenes(ctx, a.nokakao if alive(a.nokakao) else None, a.nollm if alive(a.nollm) else None)
        ctx.close()
        if not only or "mobile" in only:
            print("[mobile]"); mobile(browser, a.base)
        if not a.no_video and (not only or "video" in only):
            print("[video]"); out = demo_video(browser, a.base); print("  video:", out)
        browser.close()
    print(f"done in {time.time() - t0:.0f}s → {SHOTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
