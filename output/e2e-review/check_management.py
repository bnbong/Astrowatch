import json,uuid,requests
from pathlib import Path
O=Path(__file__).parent; B='http://127.0.0.1:8000'; result={}; created=[]
def req(name,method,path,body=None):
    r=requests.request(method,B+path,json=body,timeout=30)
    result[name]={'status':r.status_code,'body':r.json() if r.content else None}; return result[name]['body']
try:
    body={'event_title':'E2E REVIEW '+uuid.uuid4().hex[:8],'start_dt':'2031-01-01T22:00:00','end_dt':'2031-01-02T02:00:00','location_label':'서울'}
    a=req('create','POST','/api/plans',body); created.append(a['plan']['id'])
    b=req('overlap','POST','/api/plans',body); created.append(b['plan']['id'])
    req('detail','GET','/api/plans/'+str(created[0]))
    req('invalid_update','PATCH','/api/plans/'+str(created[0]),{'end_dt':'2031-01-01T21:00:00'})
    req('update','PATCH','/api/plans/'+str(created[0]),{'event_title':'E2E 수정 완료'})
    sid='review-delete-'+uuid.uuid4().hex
    req('chat_for_log','POST','/api/chat',{'session_id':sid,'message':'안녕'})
    req('log_detail','GET','/api/logs/'+sid)
    req('log_edit_rejected','PATCH','/api/logs/'+sid,{'message':'modified'})
    req('log_delete','DELETE','/api/logs/'+sid)
    req('log_after_delete','GET','/api/logs/'+sid)
finally:
    for pid in created: req('delete_'+str(pid),'DELETE','/api/plans/'+str(pid))
    (O/'management.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print({k:v['status'] for k,v in result.items()})
