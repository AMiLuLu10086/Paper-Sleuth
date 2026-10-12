from pathlib import Path
import json
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8',errors='replace')
root = Path(__file__).resolve().parents[1]
output = root/'test-results'
output.mkdir(exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True,executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
    page = browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
    errors = []
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8000')
    page.wait_for_load_state('networkidle')
    page.get_by_role('heading',name='与文献对话').wait_for()
    page.screenshot(path=str(output/'desktop.png'),full_page=True)
    print(json.dumps({'page_title':page.title(),'document_rows':page.locator('.paper-row').count(),'page_errors':errors},ensure_ascii=False),flush=True)
    page.get_by_role('button',name='设置',exact=True).click()
    page.get_by_role('dialog',name='系统设置').wait_for()
    page.get_by_role('button',name='关闭设置').click()
    if page.locator('.paper-row').count():
        page.locator('.paper-main').first.click()
        page.locator('canvas').wait_for()
        page.wait_for_selector('.pdf-loading',state='hidden',timeout=30000)
        page.wait_for_function("document.querySelector('canvas')?.height > 300")
        page.screenshot(path=str(output/'paper-preview.png'),full_page=True)
        page.get_by_role('button',name='图表',exact=True).click()
        page.locator('.element-card').first.wait_for(timeout=15000)
        print('Figure/table cards:',page.locator('.element-card').count(),flush=True)
        page.get_by_role('button',name='清洗记录',exact=True).click()
        page.locator('.cleaning-entry').first.wait_for()
        # 用本地真实召回的坐标验证引用点击；仅替换生成响应，不调用云端、不保存假回答。
        papers=page.request.get('http://127.0.0.1:8000/api/documents').json()
        paper_id=papers[0]['id']
        sources=page.request.post('http://127.0.0.1:8000/api/search',data={'question':'Fig. 5 所示的反应机理是什么？','document_ids':[paper_id]}).json()['sources']
        assert sources
        source=sources[0]
        def health_route(route):
            response=route.fetch()
            payload=response.json(); payload['llm_configured']=True
            route.fulfill(response=response,json=payload)
        page.route('**/api/health',health_route)
        page.route('**/api/chats',lambda route: route.fulfill(json={'id':'browser-fixture','title':'Browser fixture'} if route.request.method=='POST' else []))
        answer='这是浏览器自动化的受控测试响应，用于验证引用定位。[S1]'
        frames=[('sources',{'sources':[source]}),('token',{'text':answer}),('done',{'content':answer,'sources':[source]})]
        body=''.join('event: '+event+'\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n' for event,data in frames)
        page.route('**/api/chats/browser-fixture/messages',lambda route: route.fulfill(content_type='text/event-stream',body=body))
        # health 每 2.5 秒刷新；条件使用实际点击结果轮询，不靠固定等待。
        page.get_by_label('输入文献问题').fill('Fig. 5 所示的反应机理是什么？')
        for _ in range(4):
            page.get_by_role('button',name='发送问题').click()
            try:
                page.locator('.inline-citation').wait_for(timeout=3000)
                break
            except Exception:
                continue
        page.locator('.inline-citation').click()
        page.get_by_role('button',name='原文',exact=True).click()
        page.wait_for_selector('.pdf-loading',state='hidden',timeout=30000)
        assert page.locator('.pdf-highlight').count()>0
        assert int(page.get_by_label('PDF 页码').input_value())==source['pages'][0]
        page.screenshot(path=str(output/'citation-preview.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('button',name='关闭原文预览').click()
    page.screenshot(path=str(output/'mobile.png'),full_page=True)
    assert not errors,errors
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    browser.close()
