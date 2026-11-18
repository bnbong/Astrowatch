import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path('tmp/review_deps').resolve()))
from playwright.sync_api import sync_playwright
OUT=Path(__file__).parent
results={}
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8000/')
    page.locator('#chat-input').fill('2026년 12월 14일 서울에서 밤 10시부터 다음 날 새벽 2시까지 쌍둥이자리 유성우 관측 계획을 짜줘.')
    with page.expect_response(lambda r:'/api/chat' in r.url,timeout=240000) as response:
        page.locator('#send-btn').click()
        results['busy_visible']=page.locator('#status').is_visible()
    data=response.value.json(); results['chat']=data
    page.locator('#send-btn').wait_for(state='visible'); page.wait_for_timeout(500)
    page.screenshot(path=str(OUT/'browser-chat.png'),full_page=True)
    results['cards']=page.locator('.save-btn').count()
    results['timeline_text']=page.locator('#timeline').inner_text()
    if results['cards']:
        with page.expect_response(lambda r:'/api/plans' in r.url and r.request.method=='POST') as saved:
            page.locator('.save-btn').first.click()
        saved_data=saved.value.json(); results['save']=saved_data; pid=saved_data['plan']['id']
        page.goto('http://127.0.0.1:8000/plans.html')
        title=saved_data['plan']['event_title']; page.locator('#plans-body tr').filter(has_text=title).last.click()
        page.locator('input[name="event_title"]').fill('E2E 브라우저 테스트 임시 계획')
        with page.expect_response(lambda r:r.request.method=='PATCH') as edited:
            page.locator('#edit-form button[type="submit"]').click()
        results['edit']=edited.value.json()
        page.screenshot(path=str(OUT/'browser-plan.png'),full_page=True)
        page.locator('#delete-btn').click()
        with page.expect_response(lambda r:r.request.method=='DELETE') as deleted: page.locator('#confirm-yes').click()
        results['delete_status']=deleted.value.status
    page.goto('http://127.0.0.1:8000/logs.html')
    page.locator('#sessions-body tr').first.wait_for()
    results['log_rows']=page.locator('#sessions-body tr').count()
    page.locator('#sessions-body tr').first.click()
    page.locator('.events').wait_for()
    page.screenshot(path=str(OUT/'browser-logs.png'),full_page=True)
    page.goto('http://127.0.0.1:8000/docs'); page.screenshot(path=str(OUT/'browser-openapi.png'),full_page=True)
    results['page_errors']=errors
    browser.close()
(OUT/'browser-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in results.items() if k not in ['chat','timeline_text']},ensure_ascii=False))
