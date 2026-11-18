"""판정 순수 함수 (F3008) — 네트워크·LLM 없음."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from astrowatch.agent.judge import forecast_hours, judge_candidates, judge_one, rank_judged
from astrowatch.schemas.domain import Candidate, Conditions, Verdict
from astrowatch.tools.base import ErrorCode, fail, ok
from astrowatch.tools.contracts import EventKind, HourlySky, NightWindowOutput, SkyCondition, SkyForecastOutput

D = dt.date(2026, 12, 14)
SETTINGS = SimpleNamespace(min_obs_minutes=30)


def night(**kw) -> NightWindowOutput:
    base = dict(date=D, sunset="17:15", astro_dusk_end="18:48", astro_dawn_start="06:05", sunrise_next="07:40",
                moonrise="11:20", moonset="21:52", lun_age=5.5, dark_minutes=677)
    base.update(kw)
    return NightWindowOutput(**base)


def cand(kind=EventKind.METEOR, minute: int | None = 23 * 60, title="쌍둥이자리 유성우") -> Candidate:
    return Candidate(event_id="20261214-1", title=title, kind=kind, date=D, region="서울",
                     lat=37.5665, lon=126.978, event_minute=minute)


R1 = Conditions(date_from=D, date_to=D, region="서울", start_minute=22 * 60, end_minute=26 * 60,
                event_kind=EventKind.METEOR, event_query="쌍둥이자리 유성우")


def forecast(sky: SkyCondition, pop: int = 10, hours=range(22, 26)):
    return ok(SkyForecastOutput(date=D, source="mock", hourly=[HourlySky(hour=h, sky=sky, pop=pop) for h in hours],
                                coverage="full"))


def test_r1_conditional_out_of_range():
    fc = fail(ErrorCode.OUT_OF_RANGE, "예보 범위 밖")
    [j] = judge_candidates([cand()], R1, {D: night()}, {D: fc}, SETTINGS)
    assert j.verdict == Verdict.CONDITIONAL
    assert (j.time_window.start, j.time_window.end) == (1320, 1560)
    assert (j.dark_window.start, j.dark_window.end) == (18 * 60 + 48, 1440 + 6 * 60 + 5)
    assert j.moon_interferes is False
    assert j.latest_arrival_minute == 1560 - 30
    assert any("D-3" in r for r in j.reasons)
    assert j.clear_minutes is None


def test_cloudy_unfit():
    j = judge_one(cand(), R1, night(), forecast(SkyCondition.CLOUDY), 30)
    assert j.verdict == Verdict.UNFIT
    assert j.clear_minutes == 0
    assert any("맑은 시간 0분" in r for r in j.reasons)
    assert j.worst_sky == SkyCondition.CLOUDY


def test_clear_fit():
    j = judge_one(cand(), R1, night(), forecast(SkyCondition.CLEAR), 30)
    assert j.verdict == Verdict.FIT
    assert j.clear_minutes == 240
    assert j.latest_arrival_minute == 1560 - 30
    assert j.forecast_source == "mock"


def test_high_pop_not_clear_and_partly_option():
    j = judge_one(cand(), R1, night(), forecast(SkyCondition.CLEAR, pop=60), 30)
    assert j.verdict == Verdict.UNFIT
    partly = forecast(SkyCondition.PARTLY)
    assert judge_one(cand(), R1, night(), partly, 30).verdict == Verdict.UNFIT
    allow = R1.model_copy(update={"allow_partly_cloudy": True})
    assert judge_one(cand(), allow, night(), partly, 30).verdict == Verdict.FIT


def test_full_moon_meteor_unfit():
    n = night(lun_age=14.0, moonrise="16:50", moonset="07:30")
    j = judge_one(cand(), R1, n, forecast(SkyCondition.CLEAR), 30)
    assert j.verdict == Verdict.UNFIT
    assert j.moon_interferes is True
    assert any("달빛(월령 14일)" in r for r in j.reasons)
    # 유성우가 아니면 사유만 남기고 등급 유지
    j2 = judge_one(cand(kind=EventKind.PLANET, minute=None, title="목성 충"), R1, n, forecast(SkyCondition.CLEAR), 30)
    assert j2.verdict == Verdict.FIT and j2.moon_interferes is True


def test_no_night_window_unjudged():
    [j] = judge_candidates([cand()], R1, {D: None}, {}, SETTINGS)
    assert j.verdict == Verdict.UNJUDGED
    assert j.reasons and j.time_window is None


def test_season_unjudged():
    j = judge_one(cand(kind=EventKind.SEASON, minute=None, title="동지"), R1, night(), None, 30)
    assert j.verdict == Verdict.UNJUDGED
    assert any("관측 대상 아님" in r for r in j.reasons)


def test_short_window_unfit():
    c = R1.model_copy(update={"start_minute": 18 * 60, "end_minute": 19 * 60})
    j = judge_one(cand(kind=EventKind.OTHER, minute=None), c, night(), None, 30)
    assert j.verdict == Verdict.UNFIT
    assert j.time_window.minutes == 12  # 18:48~19:00


def test_partial_coverage_counts_known_hours():
    fc = ok(SkyForecastOutput(date=D, source="kma_short", coverage="partial",
                              hourly=[HourlySky(hour=22, sky=SkyCondition.CLEAR, pop=0)]))
    j = judge_one(cand(), R1, night(), fc, 30)
    assert j.verdict == Verdict.FIT and j.clear_minutes == 60
    assert any("확인 불가" in r for r in j.reasons)
    assert j.latest_arrival_minute == 23 * 60 - 30


def test_forecast_hours_from_window():
    assert forecast_hours(R1, night()) == [22, 23, 24, 25]
    assert forecast_hours(Conditions(), night())[0] == 18


def test_pop_unknown_is_not_clear():
    fc = ok(SkyForecastOutput(date=D, source="kma_mid", coverage="full",
                              hourly=[HourlySky(hour=h, sky=SkyCondition.CLEAR, pop=None) for h in range(22, 26)]))
    j = judge_one(cand(), R1, night(), fc, 30)
    assert j.verdict == Verdict.CONDITIONAL
    assert j.clear_minutes == 0


def test_partial_latest_arrival_skips_cloudy():
    skies = {22: (SkyCondition.CLOUDY, 10), 23: (SkyCondition.CLOUDY, 10), 24: (SkyCondition.CLEAR, None),
             25: (SkyCondition.CLOUDY, 10)}
    fc = ok(SkyForecastOutput(date=D, source="kma_short", coverage="full",
                              hourly=[HourlySky(hour=h, sky=s, pop=p) for h, (s, p) in skies.items()]))
    j = judge_one(cand(), R1, night(), fc, 30)
    assert j.verdict == Verdict.CONDITIONAL
    assert j.latest_arrival_minute == 1440 + 30   # 00:30 — 흐림 확인된 01~02시는 제외
    assert any("날씨 미확인 구간 기준" in r for r in j.reasons)


def test_moon_after_midnight_uses_next_day():
    oor = fail(ErrorCode.OUT_OF_RANGE, "범위 밖")
    # 오늘은 낮에만 떠 있고, 다음 날 정보가 없으면 자정 이후 달은 확인 불가
    n = night(lun_age=14.0, moonrise="03:00", moonset="15:00")
    j = judge_one(cand(), R1, n, oor, 30)
    assert j.moon_interferes is None and j.verdict == Verdict.CONDITIONAL
    assert any("확인 불가" in r for r in j.reasons)
    # 다음 날 00:30 월출이면 관측 창(~02:00)에 걸림
    n2 = night(lun_age=14.0, moonrise="03:00", moonset="15:00", moonrise_next="00:30", moonset_next="15:40")
    j2 = judge_one(cand(), R1, n2, oor, 30)
    assert j2.moon_interferes is True and j2.verdict == Verdict.UNFIT
    # 다음 날 03:40 월출이면 걸리지 않음
    n3 = night(lun_age=14.0, moonrise="03:00", moonset="15:00", moonrise_next="03:40", moonset_next="15:40")
    assert judge_one(cand(), R1, n3, oor, 30).moon_interferes is False


def _deep() -> Candidate:
    return cand(kind=EventKind.DEEP_SKY, minute=None, title="안드로메다 은하")


def test_deep_sky_moon_all_night_unfit():
    n = night(lun_age=14.0, moonrise="16:50", moonset="07:30", moonrise_next="17:40", moonset_next="08:20")
    j = judge_one(_deep(), Conditions(region="서울"), n, None, 30)
    assert j.verdict == Verdict.UNFIT
    assert any("희미한 천체" in r for r in j.reasons)


def test_deep_sky_cut_after_moonset():
    n = night(lun_age=12.0, moonrise="12:00", moonset="22:00", moonrise_next="12:50", moonset_next="23:10")
    j = judge_one(_deep(), Conditions(region="서울"), n, None, 30)
    assert j.verdict == Verdict.CONDITIONAL
    assert (j.time_window.start, j.time_window.end) == (22 * 60, 1440 + 6 * 60 + 5)
    assert j.moon_interferes is False      # 최종(잘린) 창 기준으로 다시 계산 — 잘린 창에는 달이 없다
    assert any("월몰 22:00 이후 기준" in r for r in j.reasons)
    # 맑은 예보면 잘린 구간으로 충족
    fc = ok(SkyForecastOutput(date=D, source="mock", coverage="full",
                              hourly=[HourlySky(hour=h, sky=SkyCondition.CLEAR, pop=0) for h in range(18, 31) if h <= 29]))
    assert judge_one(_deep(), Conditions(region="서울"), n, fc, 30).verdict == Verdict.FIT


def test_rank_judged_order():
    def jc(day, verdict, clear, moon, minutes=240):
        from astrowatch.schemas.domain import JudgedCandidate, Window

        c = cand().model_copy(update={"date": D + dt.timedelta(days=day)})
        return JudgedCandidate(candidate=c, verdict=verdict, clear_minutes=clear, moon_interferes=moon,
                               time_window=Window(start=1320, end=1320 + minutes))

    items = [
        jc(0, Verdict.CONDITIONAL, None, False),
        jc(1, Verdict.FIT, 120, False),
        jc(2, Verdict.FIT, 240, True),
        jc(3, Verdict.FIT, 240, False),
        jc(4, Verdict.UNFIT, 0, False),
        jc(5, Verdict.FIT, 240, False),
    ]
    order = [(j.candidate.date - D).days for j in rank_judged(items)]
    assert order == [3, 5, 2, 1, 0, 4]


# ── 천체 고도 (object_windows) · 강수형태 ─────────────────────

from astrowatch.schemas.domain import Window  # noqa: E402
from astrowatch.tools.contracts import Precip  # noqa: E402

DEEP = Conditions(date_from=D, date_to=D, region="대전", event_kind=EventKind.DEEP_SKY, event_query="안드로메다 은하")


def deep_cand() -> Candidate:
    return Candidate(event_id="target-20261214", title="안드로메다 은하", kind=EventKind.DEEP_SKY, date=D,
                     region="대전", lat=36.35, lon=127.38)


M31_META = {"found": True, "name": "안드로메다 은하", "min_alt": 20, "transit_minute": 20 * 60 + 35,
            "transit_alt": 85.0, "transit_az": 180.0}


def test_object_windows_shrink_time_window():
    key = ("target-20261214", D)
    wins = {key: [Window(start=15 * 60, end=1440 + 3 * 60 + 40)]}
    [j] = judge_candidates([deep_cand()], DEEP, {D: night()}, {}, SETTINGS,
                           object_windows=wins, object_meta={key: M31_META})
    assert j.verdict == Verdict.CONDITIONAL
    assert (j.time_window.start, j.time_window.end) == (18 * 60 + 48, 1440 + 3 * 60 + 40)   # 06:05 → 03:40 로 줄어듦
    assert any("안드로메다 은하 고도 20° 이상 15:00~03:40+1, 남중 20:35(고도 85°)" in r for r in j.reasons)
    assert j.target_alt == 85.0 and j.target_az == 180.0


def test_object_windows_pick_longest_overlap():
    key = ("안드로메다 은하", D)                       # title 키도 허용
    wins = {key: [Window(start=13 * 60, end=19 * 60), Window(start=1440 + 1 * 60, end=1440 + 5 * 60)]}
    [j] = judge_candidates([deep_cand()], DEEP, {D: night()}, {}, SETTINGS, object_windows=wins)
    assert (j.time_window.start, j.time_window.end) == (1440 + 60, 1440 + 300)


def test_object_never_rises_unfit():
    key = ("target-20261214", D)
    [j] = judge_candidates([deep_cand()], DEEP, {D: night()}, {}, SETTINGS,
                           object_windows={key: []}, object_meta={key: {**M31_META, "transit_alt": 5.0}})
    assert j.verdict == Verdict.UNFIT
    assert any("그날 밤 지평선 위로 20° 이상 오르지 않음" in r for r in j.reasons)


def test_object_unknown_keeps_dark_window():
    key = ("target-20261214", D)
    [j] = judge_candidates([deep_cand()], DEEP, {D: night()}, {}, SETTINGS, object_meta={key: {"found": False}})
    assert (j.time_window.start, j.time_window.end) == (18 * 60 + 48, 1440 + 6 * 60 + 5)
    assert "천체 좌표를 몰라 고도는 판단하지 않음" in j.reasons


def test_precip_spans_summarized_and_not_clear():
    hourly = [HourlySky(hour=h, sky=SkyCondition.CLEAR, pop=20,
                        precip=Precip.SNOW if 21 <= h <= 26 else Precip.NONE) for h in range(18, 30)]
    fc = ok(SkyForecastOutput(date=D, source="kma_short", hourly=hourly, coverage="full"))
    c = Conditions(date_from=D, date_to=D, region="서울")
    j = judge_one(cand(kind=EventKind.OTHER, minute=None, title="일반 관측"), c, night(), fc, 30)
    assert "눈 예보 21:00~03:00+1" in j.reasons
    # 18:48~21:00 · 03:00~06:00 만 맑음 (눈 시각은 SKY 맑음이어도 세지 않음, 06:00~06:05 는 예보 없음)
    assert j.clear_minutes == (21 * 60 - (18 * 60 + 48)) + (6 * 60 - 3 * 60)


def test_precip_two_kinds():
    hourly = [HourlySky(hour=h, sky=SkyCondition.CLOUDY, pop=70,
                        precip={18: Precip.RAIN, 19: Precip.RAIN, 22: Precip.SLEET}.get(h, Precip.NONE))
              for h in range(18, 30)]
    fc = ok(SkyForecastOutput(date=D, source="kma_short", hourly=hourly, coverage="full"))
    j = judge_one(cand(kind=EventKind.OTHER, minute=None, title="일반 관측"), Conditions(region="서울"), night(), fc, 30)
    assert any("비 예보 18:00~20:00 · 비/눈 예보 22:00~23:00" == r for r in j.reasons)


def test_snow_safety_note():
    from astrowatch.agent.service import _safety

    j = judge_one(cand(), R1, night(), forecast(SkyCondition.CLEAR), 30)
    assert _safety([j]) == {}
    j2 = j.model_copy(update={"reasons": [*j.reasons, "눈 예보 23:00~01:00+1"]})
    assert _safety([j2])["safety_note"].endswith("눈·결빙 주의.")


def test_moon_cut_prefers_clear_segment():
    # 어두운 18:00~06:00, 달 21:00~23:00, 21시 전 맑음 · 이후 흐림 → 짧아도 맑은 18~21 구간 채택
    n = night(astro_dusk_end="18:00", astro_dawn_start="06:00", lun_age=12.0, moonrise="21:00", moonset="23:00",
              moonrise_next="21:50", moonset_next="23:50")
    hourly = [HourlySky(hour=h, sky=SkyCondition.CLEAR if h < 21 else SkyCondition.CLOUDY, pop=10)
              for h in range(18, 30)]
    fc = ok(SkyForecastOutput(date=D, source="mock", hourly=hourly, coverage="full"))
    j = judge_one(_deep(), Conditions(region="서울"), n, fc, 30)
    assert j.verdict == Verdict.FIT
    assert (j.time_window.start, j.time_window.end) == (18 * 60, 21 * 60)
    assert j.clear_minutes == 180 and j.moon_interferes is False
    # 예보가 없으면 가장 긴 구간(23~06)
    j2 = judge_one(_deep(), Conditions(region="서울"), n, None, 30)
    assert (j2.time_window.start, j2.time_window.end) == (23 * 60, 1440 + 6 * 60)


def test_moon_unknown_after_midnight_not_assumed_dark():
    # 오늘 월몰 22:00 은 알지만 다음 날 월출·월몰이 없음 → 자정 이후를 '달 없음'으로 단정하지 않는다
    n = night(lun_age=12.0, moonrise="12:00", moonset="22:00", moonrise_next=None, moonset_next=None)
    j = judge_one(_deep(), Conditions(region="서울"), n, None, 30)
    assert (j.time_window.start, j.time_window.end) == (22 * 60, 1440 + 6 * 60 + 5)
    assert j.moon_interferes is None
    assert "자정 이후 달 정보 없음 - 달빛 영향 확인 불가" in j.reasons


def test_best_viewing_rules():
    from astrowatch.agent.nodes import best_viewing

    dark = Window(start=18 * 60 + 48, end=1440 + 6 * 60 + 5)
    up = [Window(start=900, end=1670)]
    best, basis = best_viewing(dark, 21 * 60 + 23, up)                 # 남중이 어두운 창 안 → ±1시간
    assert (best.start, best.end) == (20 * 60 + 23, 22 * 60 + 23) and "남중 전후" in basis
    best, _ = best_viewing(dark, 15 * 60, [Window(start=720, end=1300)])  # 남중이 어두워지기 전 → 초저녁 끝
    assert (best.start, best.end) == (dark.start, dark.start + 60)
    best, _ = best_viewing(dark, 1440 + 9 * 60, [Window(start=1440 + 4 * 60, end=2159)])  # 남중이 날 밝은 뒤 → 새벽
    assert (best.start, best.end) == (dark.end - 60, dark.end)
    best, basis = best_viewing(dark, 13 * 60, [Window(start=720, end=17 * 60)])   # 낮에만 떠 있음
    assert best is None and "낮에만" in basis


# ── 밤 타임바 필드 (moon_up / moon_unknown / hourly_sky / object_window) ──

def test_timebar_moon_intervals_filled_regardless_of_age():
    # 월령 5.5 (밝지 않음) 여도 달이 떠 있는 구간은 싣는다. 다음 날 출몰 정보 없음 → 자정 이후는 확인 불가
    j = judge_one(cand(), R1, night(), forecast(SkyCondition.CLEAR), 30)
    assert [(w.start, w.end) for w in j.moon_up] == [(11 * 60 + 20, 21 * 60 + 52)]
    assert [(w.start, w.end) for w in j.moon_unknown] == [(1440, 2159)]   # 2159 상한
    # 그날 월출 없이 21:52 월몰 → [0, 1312). 다음 날 12:50~23:10 은 2159 상한에 잘려 빈 구간이 되므로 뺀다
    j2 = judge_one(cand(), R1, night(moonrise=None, moonrise_next="12:50", moonset_next="23:10"),
                   forecast(SkyCondition.CLEAR), 30)
    assert [(w.start, w.end) for w in j2.moon_up] == [(0, 1312)]
    assert j2.moon_unknown == []
    # 천체 고도 구간을 쓴 경우 채택한 구간이 object_window 로 실린다
    key = ("target-20261214", D)
    src = Window(start=15 * 60, end=1440 + 3 * 60 + 40)
    [j3] = judge_candidates([deep_cand()], DEEP, {D: night()}, {}, SETTINGS,
                            object_windows={key: [Window(start=8 * 60, end=10 * 60), src]},
                            object_meta={key: M31_META})
    assert j3.object_window == src
    assert j.object_window is None


def test_timebar_hourly_sky_clipped_to_window():
    hourly = [HourlySky(hour=h, sky=SkyCondition.CLEAR if h % 2 else SkyCondition.CLOUDY, pop=h,
                        precip=Precip.RAIN if h == 23 else None) for h in range(15, 30)]
    fc = ok(SkyForecastOutput(date=D, source="mock", hourly=hourly, coverage="full"))
    j = judge_one(cand(), R1, night(), fc, 30)          # 어두운 창 ∩ 내 창 = 22:00~02:00+1
    assert [h.hour for h in j.hourly_sky] == [22, 23, 24, 25]
    h23 = j.hourly_sky[1]
    assert (h23.sky, h23.pop, h23.precip) == (SkyCondition.CLEAR, 23, "rain")
    assert j.hourly_sky[0].precip is None and j.hourly_sky[0].sky == SkyCondition.CLOUDY
    # 내 시간을 지정하지 않으면 어두운 창 18:48~06:05+1 에 걸치는 18~29시 (15~17시는 제외)
    j2 = judge_one(cand(), R1.model_copy(update={"start_minute": None, "end_minute": None}), night(), fc, 30)
    assert [h.hour for h in j2.hourly_sky] == list(range(18, 30))


def test_timebar_hourly_sky_empty_without_forecast():
    assert judge_one(cand(), R1, night(), None, 30).hourly_sky == []
    oor = judge_one(cand(), R1, night(), fail(ErrorCode.OUT_OF_RANGE, "예보 범위 밖"), 30)
    assert oor.hourly_sky == []
    assert oor.moon_up                                   # 달 구간은 예보와 무관하게 채워짐
