import sys,json
from pathlib import Path
sys.path.insert(0,str(Path('tmp/review_deps').resolve()))
from playwright.sync_api import sync_playwright
O=Path(__file__).parent
with sync_playwright() as p:
    b=p.chromium.launch(channel='msedge',headless=True)
    page=b.new_page(viewport={'width':1440,'height':1000})
    results={}
    for case in ['S07','S13']:
        page.goto('http://127.0.0.1:8000/')
        page.locator('#new-chat').click()
        msg=json.loads((O/(case+'.json')).read_text(encoding='utf-8'))['message']
        page.locator('#chat-input').fill(msg)
        with page.expect_response(lambda r:'/api/chat' in r.url,timeout=180000) as r:
            page.locator('#send-btn').click()
        results[case]=r.value.json()
        page.wait_for_timeout(400)
        page.screenshot(path=str(O/('browser-'+case+'.png')),full_page=True)
    (O/'browser-findings.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    b.close()
import pymupdf
doc=pymupdf.open('C:/Users/SSAFY/Downloads/AI코어프로젝트_3회차_요구사항정의서.pdf')
for i in [10,14,16,24,25]: doc[i].get_pixmap(matrix=pymupdf.Matrix(1.2,1.2)).save(str(O/f'requirements-page-{i+1}.png'))
print('Captured actual failure screens and relevant PDF pages.')
