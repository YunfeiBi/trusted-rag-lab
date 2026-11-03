"""Local Chrome smoke checks; credentials stay in memory, screenshots have no passwords."""
import asyncio
import base64
import json
import subprocess
from pathlib import Path
import time
import uuid
import sys

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]


async def main():
    with httpx.Client(timeout=5) as health_client:
        for attempt in range(30):
            try:
                health = health_client.get('http://127.0.0.1:8000/api/health/ready')
                if health.status_code == 200 and health.json()['status'] == 'ok':
                    break
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            await asyncio.sleep(.25)
        else:
            raise RuntimeError('Backend not ready for browser check')
    browser_path = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    if not browser_path.exists():
        raise SystemExit('Chrome not found; configure browser_path for this machine.')
    output = ROOT / '.local' / 'browser-check'
    output.mkdir(parents=True, exist_ok=True)
    profile = ROOT / '.local' / ('browser-profile-' + uuid.uuid4().hex)
    process = subprocess.Popen([str(browser_path), '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=9223', '--user-data-dir='+str(profile), 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    session = None
    counter = 0
    try:
        with httpx.Client(timeout=3) as client:
            for attempt in range(60):
                try:
                    target = client.put('http://127.0.0.1:9223/json/new?http://127.0.0.1:5173').json()
                    break
                except (httpx.HTTPError, ValueError):
                    await asyncio.sleep(.25)
            else:
                raise RuntimeError('Browser debug endpoint did not start')
        session = await websockets.connect(target['webSocketDebuggerUrl'], max_size=10_000_000)

        async def rpc(method, params=None):
            nonlocal counter
            counter += 1
            current = counter
            await session.send(json.dumps({'id': current, 'method': method, 'params': params or {}}))
            while True:
                payload = json.loads(await asyncio.wait_for(session.recv(), timeout=15))
                if payload.get('id') == current:
                    if 'error' in payload:
                        raise RuntimeError('Browser protocol call failed')
                    return payload.get('result', {})

        async def evaluate(expression):
            payload = await rpc('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
            if 'exceptionDetails' in payload:
                raise RuntimeError('Browser evaluation failed')
            return payload.get('result', {}).get('value')

        async def wait_for(expression, attempts=60):
            for attempt in range(attempts):
                if await evaluate(expression):
                    return
                await asyncio.sleep(.2)
            raise RuntimeError('UI state did not appear in time')

        async def screenshot(name):
            picture = await rpc('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})
            (output / name).write_bytes(base64.b64decode(picture['data']))

        await rpc('Page.enable')
        await rpc('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
        await wait_for("document.querySelector('input[autocomplete=username]') !== null")
        await wait_for("document.querySelector('.service-status')?.textContent.includes('本机服务已就绪')")
        await wait_for("document.querySelector('.university-emblem')?.naturalWidth > 0")
        await screenshot('login.png')
        records = json.loads((ROOT / '.local' / 'demo_accounts.json').read_text(encoding='utf-8'))
        accounts = {a['username']: a for a in records['accounts']}
        for username in ['demo_admin', 'demo_member', 'demo_other']:
            account = accounts[username]
            with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=5) as api_client:
                auth = api_client.post('/auth/login',json={'username':username,'password':account['password']})
                auth.raise_for_status()
                api_headers = {'Authorization':'Bearer '+auth.json()['access_token']}
                try:
                    project_response = api_client.get('/projects',headers=api_headers)
                    project_response.raise_for_status()
                    expected = [p['name'] for p in project_response.json()]
                finally:
                    api_client.post('/auth/logout',headers=api_headers)
            await evaluate("(() => {const u=document.querySelector('input[autocomplete=username]');const p=document.querySelector('input[type=password]');u.value=" + json.dumps(username) + ";u.dispatchEvent(new Event('input',{bubbles:true}));p.value=" + json.dumps(account['password']) + ";p.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('form').requestSubmit();return true})()")
            await wait_for("document.querySelector('h1')?.textContent === '我的项目' && document.querySelectorAll('.project-card').length > 0 && !document.querySelector('header button.primary:disabled')")
            names = await evaluate("Array.from(document.querySelectorAll('.project-card h2')).map(e=>e.textContent)")
            assert names == expected, 'UI project scope differs from server-authorized projects'
            if username == 'demo_member':
                assert await evaluate("document.querySelector('.member-form') === null"), 'Member management exposed'
            await screenshot(username + '.png')
            assert await evaluate("document.querySelectorAll('[data-nav]').length===7 && document.querySelector('.sidebar-emblem')?.naturalWidth>0"), 'Seven pages or university emblem missing'
            if username == 'demo_admin' and '--centers' in sys.argv:
                report=json.loads((ROOT/'.local/centers-live-check.json').read_text(encoding='utf-8'))
                await evaluate("Array.from(document.querySelectorAll('.project-card')).find(e=>e.querySelector('h2').textContent==="+json.dumps(report['project_name'])+").click()")
                await wait_for("!document.querySelector('[data-nav=documents]:disabled')")
                await evaluate("document.querySelector('[data-nav=documents]').click()")
                await wait_for("document.querySelector('.pdf-password') !== null && document.querySelectorAll('.documents-table tbody tr').length===2")
                upload_file=ROOT/'.local/centers-check/synthetic-encrypted.pdf'
                tree=await rpc('DOM.getDocument')
                node=await rpc('DOM.querySelector',{'nodeId':tree['root']['nodeId'],'selector':'.upload-area input[type=file]'})
                await rpc('DOM.setFileInputFiles',{'nodeId':node['nodeId'],'files':[str(upload_file)]})
                await evaluate("(() => {const p=document.querySelector('.pdf-password');p.value='deliberately-wrong';p.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.upload-area input[type=checkbox]').click();document.querySelector('.upload-area').requestSubmit()})()")
                await wait_for("document.querySelector('.document-library .error')?.textContent.includes('密码错误')",attempts=150)
                assert await evaluate("document.querySelector('.pdf-password').value===''")
                await asyncio.sleep(3.4)
                assert await evaluate("document.querySelector('.document-library .error')?.textContent.includes('密码错误')"), 'Polling must not erase upload feedback'
                await screenshot('pdf-upload-desktop.png')
                for name,selector in [('tasks','.task-center'),('evaluation','.evaluation-center'),('runtime','.runtime-center'),('search','#evidence-search'),('questions','.question-panel')]:
                    await evaluate("document.querySelector('[data-nav="+name+"]').click()")
                    await wait_for("document.querySelector("+json.dumps(selector)+") !== null")
                    await wait_for("!document.querySelector('[data-nav="+name+"]:disabled')")
                    assert await evaluate("document.querySelector('main').dataset.page==="+json.dumps(name)+" && document.querySelector('.project-selector').value==="+json.dumps(report['project_id']))
                    if name=='tasks':
                        await wait_for("document.querySelectorAll('.task-center tbody tr').length===2")
                    if name=='evaluation':
                        await wait_for("document.querySelectorAll('.eval-case').length>0")
                        assert await evaluate("document.querySelector('.eval-case').textContent.includes('待人工确认') && document.querySelector('.eval-case button').disabled")
                        previous_cases=await evaluate("document.querySelectorAll('.eval-case').length")
                        await evaluate("(() => {const select=document.querySelector('.case-form select');const docs=document.querySelectorAll('.case-form select')[1];docs.value=docs.options[1].value;docs.dispatchEvent(new Event('change',{bubbles:true}))})()")
                        await wait_for("document.querySelector('.gold-picker input') !== null")
                        await evaluate("(() => {const q=document.querySelector('.case-form input');q.value='浏览器自动合成标注草稿';q.dispatchEvent(new Event('input',{bubbles:true}));const notes=document.querySelector('.case-form textarea');notes.value='AI自动合成测试，未人工确认，不用于正式评测。';notes.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.gold-picker input').click();document.querySelector('.case-form').requestSubmit()})()")
                        await wait_for("document.querySelectorAll('.eval-case').length==="+str(previous_cases+1))
                    if name=='runtime':
                        await wait_for("document.querySelectorAll('.audit-table tbody tr').length>=4")
                    if name in {'tasks','evaluation','runtime'}:
                        await evaluate('window.scrollTo(0,0)')
                        await screenshot('seven-'+name+'-desktop.png')
                        await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                        assert await evaluate('document.documentElement.scrollWidth <= 390'), 'Seven-page mobile overflow: '+name
                        await screenshot('seven-'+name+'-mobile.png')
                        await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                await evaluate("document.querySelector('[data-nav=projects]').click()")
                await wait_for("document.querySelector('.project-card') !== null && !document.querySelector('[data-nav=projects]:disabled')")
                print('PASS seven isolated pages, university emblem, password failure clearing, task/audit data, eval draft guard and mobile widths')
            if username == 'demo_admin' and '--runtime' in sys.argv:
                await evaluate("document.querySelector('[data-nav=documents]').click()")
                await wait_for("document.querySelector('[data-worker-status=recent_heartbeat]') !== null")
                assert await evaluate("document.querySelector('.worker-status').textContent.includes('本项目任务') && !document.querySelector('.project-grid')"), 'Worker status missing from dedicated documents page'
                await screenshot('runtime-desktop.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert await evaluate('window.innerWidth === 390 && document.documentElement.scrollWidth <= 390'), 'Mobile diagnostics page overflows'
                await screenshot('runtime-mobile.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                await evaluate("document.querySelector('[data-nav=projects]').click()")
                await wait_for("document.querySelector('.project-card') !== null && !document.querySelector('[data-nav=projects]:disabled')")
                print('PASS worker heartbeat and project queue diagnostics, desktop and mobile')
            if username == 'demo_admin' and '--ingestion' in sys.argv:
                await evaluate("document.querySelector('[data-nav=documents]').click()")
                await wait_for("document.querySelector('main[data-page=documents]') && !document.querySelector('.project-grid') && !document.querySelector('#evidence-search')")
                upload_file = output / 'browser-upload.md'
                upload_file.write_text('# 浏览器合成验收\n\n这是自动生成的公开演示文字，用于验证上传、处理状态和原文预览。',encoding='utf-8')
                await wait_for("document.querySelector('.upload-area input[type=file]') !== null")
                previous_version = await evaluate("(() => {const row=Array.from(document.querySelectorAll('.documents-table tbody tr')).find(e=>e.querySelector('strong')?.textContent==='browser-upload.md');if(row){const n=Number(row.querySelector('.badge').textContent.match(/v(\\d+)/)?.[1] || 0);Array.from(row.querySelectorAll('button')).find(e=>e.textContent==='更新').click();return n}return 0})()")
                tree = await rpc('DOM.getDocument')
                node = await rpc('DOM.querySelector',{'nodeId':tree['root']['nodeId'],'selector':'.upload-area input[type=file]'})
                await rpc('DOM.setFileInputFiles',{'nodeId':node['nodeId'],'files':[str(upload_file)]})
                await evaluate("document.querySelector('.upload-area input[type=checkbox]').click()")
                await evaluate("document.querySelector('.upload-area').requestSubmit()")
                for attempt in range(120):
                    state = await evaluate("(() => {const row=Array.from(document.querySelectorAll('.documents-table tbody tr')).find(e=>e.querySelector('strong')?.textContent==='browser-upload.md');return !!row && row.querySelector('.badge').textContent.includes('v"+str(previous_version+1)+" · 已就绪') && document.querySelector('.upload-area input[type=file]').files.length===0})()")
                    if state:
                        break
                    await asyncio.sleep(.5)
                else:
                    raise RuntimeError('Browser upload did not become ready')
                await evaluate("(() => {const row=Array.from(document.querySelectorAll('.documents-table tbody tr')).find(e=>e.querySelector('strong')?.textContent==='browser-upload.md');Array.from(row.querySelectorAll('button')).find(e=>e.textContent==='版本与原文').click()})()")
                await wait_for("document.querySelector('.version-panel .version-row') !== null")
                await evaluate("document.querySelector('.version-panel .version-row button').click()")
                await wait_for("document.querySelector('.markdown-source')?.textContent.includes('浏览器合成验收') && document.querySelectorAll('.chunk-card').length > 0")
                await screenshot('documents-admin.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
                await screenshot('documents-mobile.png')
                assert await evaluate('window.innerWidth === 390 && document.documentElement.scrollWidth <= 390'), 'Mobile document page overflows'
                await rpc('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
                print('PASS browser file upload, consent, Worker status, version history and source preview')
            if username == 'demo_admin' and '--search' in sys.argv:
                search_report = json.loads((ROOT / '.local' / 'search-live-check.json').read_text(encoding='utf-8'))
                await evaluate("document.querySelector('[data-nav=projects]').click()")
                await wait_for("document.querySelector('.project-card') !== null && !document.querySelector('[data-nav=projects]:disabled')")
                await evaluate("(() => {const card=Array.from(document.querySelectorAll('.project-card')).find(e=>e.querySelector('h2').textContent==="+json.dumps(search_report['project_name'])+");if(!card)throw new Error('Synthetic search project not found');card.click()})()")
                await wait_for("!document.querySelector('[data-nav=search]:disabled')")
                await evaluate("document.querySelector('[data-nav=search]').click()")
                await wait_for("document.querySelector('#evidence-search .muted')?.textContent.includes("+json.dumps(search_report['project_name'])+")")
                await evaluate("(() => {const field=document.querySelector('.search-query');field.value='项目资料通过什么方式控制访问权限？';field.dispatchEvent(new Event('input',{bubbles:true}));const mode=document.querySelector('.search-options select');mode.value='keyword';mode.dispatchEvent(new Event('change',{bubbles:true}))})()")
                await wait_for("document.querySelector('.search-consent') === null")
                await evaluate("document.querySelector('.search-form').requestSubmit()")
                await wait_for("document.querySelectorAll('.evidence-card').length>0 && document.querySelector('.search-details')?.textContent.includes('重排序状态：not_requested')")
                await screenshot('search-keyword.png')
                await evaluate("(() => {const mode=document.querySelector('.search-options select');mode.value='hybrid';mode.dispatchEvent(new Event('change',{bubbles:true}))})()")
                await wait_for("document.querySelector('.search-consent input') !== null")
                await evaluate("document.querySelector('.search-consent input').click();document.querySelector('.search-form').requestSubmit()")
                await wait_for("document.querySelectorAll('.evidence-card').length>0 && document.querySelector('.search-details')?.textContent.includes('重排序状态：applied')")
                await evaluate("document.querySelector('.evidence-card .source-button').click()")
                await wait_for("document.querySelectorAll('.source-preview .focused-line').length>0")
                await screenshot('search-hybrid.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert await evaluate('window.innerWidth === 390 && document.documentElement.scrollWidth <= 390'), 'Mobile search page overflows'
                await screenshot('search-mobile.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                print('PASS browser local keyword, real hybrid rerank, source line highlighting and mobile search width')
            if username == 'demo_admin' and '--questions' in sys.argv:
                report = json.loads((ROOT / '.local/qa-live-check.json').read_text(encoding='utf-8'))
                await evaluate("document.querySelector('[data-nav=projects]').click()")
                await wait_for("document.querySelector('.project-card') !== null && !document.querySelector('[data-nav=projects]:disabled')")
                await evaluate("Array.from(document.querySelectorAll('.project-card')).find(e=>e.querySelector('h2').textContent==="+json.dumps(report['project_name'])+").click()")
                await wait_for("!document.querySelector('[data-nav=questions]:disabled')")
                await evaluate("document.querySelector('[data-nav=questions]').click()")
                await wait_for("document.querySelector('.question-form') !== null && document.querySelectorAll('.history-item').length>=2")
                assert await evaluate("!document.querySelector('.project-grid') && !document.querySelector('.upload-area') && !document.querySelector('#evidence-search') && location.hash.startsWith('#/questions?project=')"), 'Pages must be separate'
                await evaluate("history.back()")
                await wait_for("document.querySelector('main[data-page=projects]') !== null")
                await evaluate("history.forward()")
                await wait_for("document.querySelector('.question-form') !== null && !document.querySelector('.ask-submit')?.textContent.includes('正在')")
                await evaluate("(() => {const field=document.querySelector('.question-input');field.value='项目管理员能做哪些成员管理操作？';field.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.qa-consent input').click();document.querySelector('.question-form').requestSubmit()})()")
                await wait_for("document.querySelector('.answer-block[data-status=answered]') !== null", attempts=450)
                await evaluate("document.querySelector('.answer-claims .citation-button').click()")
                await wait_for("document.querySelectorAll('.source-preview .focused-line').length>0")
                await screenshot('questions-desktop.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert await evaluate('window.innerWidth === 390 && document.documentElement.scrollWidth <= 390'), 'Mobile questions page overflows'
                await screenshot('questions-mobile.png')
                await evaluate("document.querySelector('[data-mobile-nav=documents]').click()")
                await wait_for("document.querySelector('main[data-page=documents]') !== null && !document.querySelector('.question-panel') && document.querySelector('.project-selector')?.value==="+json.dumps(report['project_id']))
                await screenshot('documents-page-mobile.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                await screenshot('documents-page-desktop.png')
                print('PASS separate pages, project context, back/forward, real QA, citations, private history and mobile navigation')
            await evaluate("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='退出登录').click()")
            await wait_for("document.querySelector('.login-card input[type=password]') !== null")
            print('PASS browser login, project scope and logout: ' + username)
        await rpc('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
        await screenshot('login-mobile.png')
        assert await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
        print('PASS mobile login layout width; screenshots in .local/browser-check')
        (output / 'result.json').write_text(json.dumps({'status': 'passed', 'users_checked': list(accounts), 'ingestion_checked':'--ingestion' in sys.argv, 'search_checked':'--search' in sys.argv, 'questions_checked':'--questions' in sys.argv, 'runtime_checked':'--runtime' in sys.argv,'centers_checked':'--centers' in sys.argv, 'checked_at': time.time()}, indent=2), encoding='utf-8')
        await rpc('Browser.close')
    finally:
        if session:
            await session.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == '__main__':
    asyncio.run(main())
