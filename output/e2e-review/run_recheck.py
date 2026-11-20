import json, time, uuid
from pathlib import Path
import requests

OUT = Path(__file__).resolve().parent

def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def turn(n, sid=None, port=8000):
    old = json.loads((OUT / (n+'.json')).read_text(encoding='utf-8')) if n != 'S20' else None
    msg = old['message'] if old else '2026년 12월 14일 서울에서 밤 10시부터 다음 날 새벽 2시까지 쌍둥이자리 유성우 관측 계획을 짜고 근처 천문대도 20km 이내로 찾아줘.'
    sid = sid or 'recheck-'+uuid.uuid4().hex
    base = f'http://127.0.0.1:{port}'
    payload = {'session_id': sid, 'message': msg}
    headers = {'X-Requested-With': 'Astrowatch', 'Origin': base}
    record = {'id': n, 'message': msg, 'request': {'method':'POST','url':base+'/api/chat','headers':headers,'json':payload}, 'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    start = time.monotonic()
    try:
        r = requests.post(base+'/api/chat', json=payload, headers=headers, timeout=240)
        record.update(status=r.status_code, seconds=round(time.monotonic()-start,2), response=r.json())
    except Exception as e:
        record.update(error=str(e), seconds=round(time.monotonic()-start,2))
    save('recheck-'+n+'.json',record)
    try:
        r = requests.get(base+'/api/logs/'+sid, timeout=20)
        save('recheck-'+n+'-logs.json', {'status':r.status_code,'response':r.json()})
    except Exception as e:
        save('recheck-'+n+'-logs.json', {'error':str(e)})
    d = record.get('response',{})
    print(json.dumps({'id':n,'status':record.get('status'),'seconds':record['seconds'], **{k:d.get(k) for k in ['intent','stop_reason','replan_count','replan_limit_reached','needs_clarification','warnings','conditions','answer']},'recommended_count':len(d.get('recommended',[]))}, ensure_ascii=False),flush=True)
    return sid

if __name__ == '__main__':
    for n in ['S07','S10','S11','S13','S18','S09']:
        turn(n)
    turn('S20',port=8003)
    sid = turn('S03')
    for n in ['S14','S15','S16']:
        turn(n,sid)
