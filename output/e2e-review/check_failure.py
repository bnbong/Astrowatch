import os,sys,subprocess,time,json,socket
from pathlib import Path
import requests
O=Path(__file__).parent.resolve(); ROOT=O.parents[1]
with socket.socket() as s: s.bind(('127.0.0.1',8001))
env=os.environ.copy(); env.update(KAKAO_REST_KEY='',DB_PATH=str(O/'isolated-failure.db'),PYTHONIOENCODING='utf-8')
log=(O/'isolated-server.txt').open('w',encoding='utf-8')
proc=subprocess.Popen([sys.executable,'-m','uvicorn','astrowatch.app.main:app','--host','127.0.0.1','--port','8001'],cwd=ROOT/'backend',env=env,stdout=log,stderr=log)
try:
    for _ in range(60):
        try:
            if requests.get('http://127.0.0.1:8001/api/health',timeout=1).ok: break
        except requests.RequestException: pass
        time.sleep(.5)
    t=time.monotonic()
    r=requests.post('http://127.0.0.1:8001/api/chat',json={'message':'2026년 12월 14일 서울에서 밤 10시부터 다음 날 새벽 2시까지 쌍둥이자리 유성우 관측 계획을 짜고 근처 천문대도 20km 이내로 찾아줘.'},timeout=180)
    d=r.json(); out={'status':r.status_code,'seconds':round(time.monotonic()-t,2),'response':d,'injection':'KAKAO_REST_KEY empty; separate process and DB; other real APIs unchanged'}
    (O/'S20-isolated-auth.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    logs=requests.get('http://127.0.0.1:8001/api/logs/'+d['session_id']).json()
    (O/'S20-isolated-auth-logs.json').write_text(json.dumps(logs,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False))
finally:
    proc.terminate(); proc.wait(timeout=15); log.close()
