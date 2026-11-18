"""Astrowatch 외부 API 1회 호출 점검 (기획 검증 2.7). 키는 출력하지 않는다. 응답은 samples/ 에 키를 지운 채 저장.

    python -X utf8 backend/scripts/check_apis.py .
"""
import json, os, re, sys, urllib.request, urllib.parse, urllib.error, datetime as dt
from pathlib import Path

PROJ = Path(sys.argv[1])
SAMPLES = PROJ / "samples"
SAMPLES.mkdir(exist_ok=True)

def load_env(p):
    env = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = re.split(r"\s+#", v, 1)[0].strip().strip('"').strip("'")
        env[k.strip()] = v
    return env

E = load_env(PROJ / ".env")
KEY = E.get("DATA_GO_KR_KEY", "")
KEY_WAS_ENCODED = "%" in KEY
if KEY_WAS_ENCODED:  # 사용자가 Encoding 키를 넣은 경우 → Decoding 값으로 되돌린다
    KEY = urllib.parse.unquote(KEY)
print("DATA_GO_KR_KEY 형식:", "Encoding(자동 변환함)" if KEY_WAS_ENCODED else "Decoding")
KAKAO = E.get("KAKAO_REST_KEY", "")
OWM = E.get("OPENWEATHER_KEY", "")
GMS = E.get("GMS_KEY", "")
BASE = E.get("OPENAI_BASE_URL", "").rstrip("/")
MODEL = E.get("LLM_MODEL", "gpt-6-luna")

def scrub(text):
    for secret in (KEY, KAKAO, OWM, GMS, urllib.parse.quote(KEY, safe="")):
        if secret:
            text = text.replace(secret, "<REDACTED>")
    return text

def call(name, url, params=None, headers=None, body=None, raw_key=False):
    if params:
        if raw_key and "serviceKey" in params:
            sk = params.pop("serviceKey")
            url = url + "?serviceKey=" + sk + "&" + urllib.parse.urlencode(params)
        else:
            url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers or {}, data=body, method="POST" if body else "GET")
    t0 = dt.datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, text = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        status, text = 0, f"{type(e).__name__}: {e}"
    ms = int((dt.datetime.now() - t0).total_seconds() * 1000)
    text = scrub(text)
    ext = "json" if text.lstrip().startswith(("{", "[")) else "xml" if text.lstrip().startswith("<") else "txt"
    (SAMPLES / f"{name}.{ext}").write_text(text, encoding="utf-8")
    return status, ms, text

def summarize(name, status, ms, text):
    code = re.search(r"<resultCode>(.*?)</resultCode>", text) or re.search(r'"resultCode"\s*:\s*"(.*?)"', text)
    msg = re.search(r"<resultMsg>(.*?)</resultMsg>", text) or re.search(r"<errMsg>(.*?)</errMsg>", text) or re.search(r'"resultMsg"\s*:\s*"(.*?)"', text)
    total = re.search(r"<totalCount>(\d+)</totalCount>", text) or re.search(r'"totalCount"\s*:\s*"?(\d+)', text)
    bits = [f"HTTP {status}", f"{ms}ms"]
    if code: bits.append(f"resultCode={code.group(1)}")
    if msg: bits.append(f"msg={msg.group(1)[:60]}")
    if total: bits.append(f"total={total.group(1)}")
    print(f"[{name}] " + " | ".join(bits))
    return code.group(1) if code else None

today = dt.date.today()
results = {}

# 1 천문현상
s, ms, t = call("kasi_astro_event", "https://apis.data.go.kr/B090041/openapi/service/AstroEventInfoService/getAstroEventInfo",
                {"serviceKey": KEY, "solYear": "2026", "solMonth": "12", "numOfRows": "50", "pageNo": "1"})
rc = summarize("천문현상 2026-12", s, ms, t)
if "NOT_REGISTERED" in t or "SERVICE_KEY_IS_NULL" in t:
    s, ms, t = call("kasi_astro_event", "https://apis.data.go.kr/B090041/openapi/service/AstroEventInfoService/getAstroEventInfo",
                    {"serviceKey": KEY, "solYear": "2026", "solMonth": "12", "numOfRows": "50", "pageNo": "1"}, raw_key=True)
    rc = summarize("천문현상 2026-12 (키 raw 재시도)", s, ms, t)
for m in re.finditer(r"<locdate>(.*?)</locdate>.*?<astroTitle>(.*?)</astroTitle>", t, re.S):
    pass
titles = re.findall(r"<astroTitle>(.*?)</astroTitle>", t)
if titles: print("   예:", " / ".join(x.strip() for x in titles[:4]))

# 2 출몰 (서울, 12/14)
s, ms, t = call("kasi_riseset_seoul_20261214", "https://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService/getLCRiseSetInfo",
                {"serviceKey": KEY, "locdate": "20261214", "longitude": "126.9780", "latitude": "37.5665", "dnYn": "Y"})
summarize("출몰 서울 12/14", s, ms, t)
for f in ("sunset", "astm", "aste", "moonrise", "moonset"):
    v = re.search(rf"<{f}>(.*?)</{f}>", t)
    if v: print(f"   {f}={v.group(1).strip()}", end="")
print()

# 3 월령
s, ms, t = call("kasi_lunage_20261214", "https://apis.data.go.kr/B090041/openapi/service/LunPhInfoService/getLunPhInfo",
                {"serviceKey": KEY, "solYear": "2026", "solMonth": "12", "solDay": "14"})
summarize("월령 12/14", s, ms, t)
v = re.search(r"<lunAge>(.*?)</lunAge>", t)
if v: print("   lunAge =", v.group(1))

# 4 단기예보 (서울 60,127) — 직전 발표 시각
now = dt.datetime.now()
slots = [2, 5, 8, 11, 14, 17, 20, 23]
cands = [h for h in slots if now >= now.replace(hour=h, minute=10, second=0, microsecond=0)]
if cands:
    bd, bt = today, cands[-1]
else:
    bd, bt = today - dt.timedelta(days=1), 23
s, ms, t = call("kma_vilage_fcst_seoul", "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
                {"serviceKey": KEY, "pageNo": "1", "numOfRows": "1000", "dataType": "JSON",
                 "base_date": bd.strftime("%Y%m%d"), "base_time": f"{bt:02d}00", "nx": "60", "ny": "127"})
summarize(f"단기예보 서울 base={bd:%m%d} {bt:02d}00", s, ms, t)
try:
    items = json.loads(t)["response"]["body"]["items"]["item"]
    sky = [i for i in items if i["category"] == "SKY"]
    print(f"   SKY 항목 {len(sky)}건, 범위 {sky[0]['fcstDate']} {sky[0]['fcstTime']} ~ {sky[-1]['fcstDate']} {sky[-1]['fcstTime']}")
except Exception as e:
    print("   파싱 실패:", type(e).__name__)

# 5 중기육상예보 (서울·인천·경기 11B00000)
tm = now.replace(hour=6, minute=0) if now.hour >= 6 and now.hour < 18 else (now.replace(hour=18, minute=0) if now.hour >= 18 else (now - dt.timedelta(days=1)).replace(hour=18, minute=0))
s, ms, t = call("kma_mid_land_fcst", "https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst",
                {"serviceKey": KEY, "pageNo": "1", "numOfRows": "10", "dataType": "JSON", "regId": "11B00000", "tmFc": tm.strftime("%Y%m%d%H%M")})
summarize(f"중기육상예보 tmFc={tm:%m%d %H%M}", s, ms, t)
try:
    it = json.loads(t)["response"]["body"]["items"]["item"][0]
    print("   wf3Pm =", it.get("wf3Pm"), "| wf5Pm =", it.get("wf5Pm"), "| wf10 =", it.get("wf10"))
except Exception as e:
    print("   파싱 실패:", type(e).__name__)

# 6 카카오 키워드 (대전 반경 20km 천문대)
s, ms, t = call("kakao_keyword_observatory_daejeon", "https://dapi.kakao.com/v2/local/search/keyword.json",
                {"query": "천문대", "x": "127.3845", "y": "36.3504", "radius": "20000", "size": "5"},
                headers={"Authorization": f"KakaoAK {KAKAO}"})
print(f"[카카오 천문대 대전 20km] HTTP {s} | {ms}ms", end="")
try:
    d = json.loads(t)["documents"]
    print(f" | {len(d)}건 | 예: " + " / ".join(f"{x['place_name']}({x['distance']}m)" for x in d[:3]))
except Exception:
    print(" |", t[:100])

# 7 OpenWeatherMap
if OWM:
    s, ms, t = call("owm_forecast_daejeon", "https://api.openweathermap.org/data/2.5/forecast",
                    {"lat": "36.3504", "lon": "127.3845", "appid": OWM, "units": "metric"})
    print(f"[OpenWeatherMap] HTTP {s} | {ms}ms", end="")
    try:
        d = json.loads(t)["list"]
        print(f" | {len(d)}개 슬롯 | {d[0]['dt_txt']} ~ {d[-1]['dt_txt']} | clouds[0]={d[0]['clouds']['all']}%")
    except Exception:
        print(" |", t[:100])

# 8 GMS chat
body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": "한 단어로 답해: 하늘 색은?"}], "max_completion_tokens": 50}).encode()
s, ms, t = call("gms_chat", f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {GMS}", "Content-Type": "application/json"}, body=body)
print(f"[GMS {MODEL}] HTTP {s} | {ms}ms", end="")
try:
    print(" | 답:", json.loads(t)["choices"][0]["message"]["content"].strip()[:40])
except Exception:
    print(" |", t[:120])

print("\nsamples/ 저장:", ", ".join(p.name for p in sorted(SAMPLES.iterdir())))
