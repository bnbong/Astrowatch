import json,time,uuid,concurrent.futures
from pathlib import Path
import requests
BASE='http://127.0.0.1:8000'
OUT=Path(__file__).parent
def save(n,x): (OUT/(n+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
def turn(n,msg,sid=None):
    sid=sid or 'review-'+uuid.uuid4().hex
    t=time.monotonic()
    try:
        r=requests.post(BASE+'/api/chat',json={'session_id':sid,'message':msg},timeout=240)
        data=r.json(); record={'id':n,'message':msg,'status':r.status_code,'seconds':round(time.monotonic()-t,2),'response':data}
        save(n,record)
        logs=requests.get(BASE+'/api/logs/'+sid,timeout=15).json(); save(n+'-logs',logs)
        print(json.dumps({'id':n,'sec':record['seconds'],'intent':data.get('intent'),'rec':len(data.get('recommended',[])),'excluded':len(data.get('excluded',[])),'replan':data.get('replan_count'),'answer':data.get('answer'),'conditions':data.get('conditions'),'warnings':data.get('warnings')},ensure_ascii=False),flush=True)
        return sid,data
    except Exception as e:
        save(n,{'id':n,'message':msg,'error':str(e),'seconds':round(time.monotonic()-t,2)}); print(n,type(e).__name__,flush=True)
        return sid,{}
cases=[
('S01','안녕, 이 서비스는 어떻게 써?'),
('S02','밤 11시 전에는 집에 돌아와야 해. 별 관측 계획을 짜줘.'),
('S03','2026년 12월 14일 서울에서 밤 10시부터 다음 날 새벽 2시까지 쌍둥이자리 유성우 관측 계획을 짜줘.'),
('S04','2026년 11월 18일 서울에서 화성은 언제 가장 잘 보여?'),
('S05','2026년 11월 18일 서울에서 밤 9시부터 11시까지 안드로메다 은하 관측 계획을 짜줘.'),
('S06','2026년 11월 18일 아틀란티스에서 밤 9시부터 11시까지 별 관측 계획을 짜줘.'),
('S07','2026년 11월 18일 서울에서 밤 9시부터 11시까지 가짜천체 XYZ-FAKE-999를 관측하는 계획을 짜줘. 다른 천체는 원하지 않아.'),
('S08','2026년 11월 18일 서울에서 낮 12시부터 오후 1시까지 유성우 관측 계획을 짜줘. 안 되면 다른 날짜나 현상이나 지역도 찾아줘.'),
('S09','2026년 11월 18일 대전 근처 천문대를 20km 이내에서 찾아줘.'),
('S10','2030년 12월 14일 서울에서 밤 10시부터 다음 날 새벽 2시까지 유성우 관측 계획을 짜줘. 날짜는 고정이야.'),
('S11','2026년 2월 30일 서울에서 밤 9시부터 11시까지 별을 보고 싶어.'),
('S12','블랙홀의 사건의 지평선이 무엇인지 근거 자료와 함께 설명해줘.'),
('S13','2026년 11월 18일 서울에서 밤 9시부터 11시까지 남십자자리 관측 계획을 짜줘. 남십자자리만 보고 싶어.'),
]
if __name__=='__main__':
    for ep in ['health','tools','graph']:
        r=requests.get(BASE+'/api/'+ep,timeout=20); save('meta-'+ep,{'status':r.status_code,'body':r.json()})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda c:turn(*c),cases))
    sid,_=results[2]
    turn('S14','같은 날 같은 시간에 장소만 대전으로 바꿔줘.',sid)
    turn('S15','방금 조건에서 종료 시각만 밤 11시로 바꿔줘.',sid)
    turn('S16','이 추천을 구글 캘린더에 등록해줘.',sid)
    for n,msg in [('S17',''),('S18',' '*3),('S19','가'*2001)]: turn(n,msg)
