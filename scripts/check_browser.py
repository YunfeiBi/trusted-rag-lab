"""Local Chrome smoke checks; credentials stay in memory, screenshots have no passwords."""
import asyncio
import base64
import json
import subprocess
from pathlib import Path
import time
import uuid
import sys
import secrets

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]


async def main():
    if '--delete-team' in sys.argv and '--accounts' not in sys.argv:
        raise SystemExit('Team deletion smoke check requires --accounts and only deletes its newly created synthetic team.')
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

        async def evaluate(expression, user_gesture=False):
            payload = await rpc('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'userGesture': user_gesture})
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
        if '--accounts' in sys.argv:
            suffix=uuid.uuid4().hex[:8]
            owner_name='注册验收甲'+suffix
            member_name='注册验收乙'+suffix
            test_password=secrets.token_urlsafe(20)
            owner_email='codex.owner.'+suffix+'@qq.com'
            member_email='codex.member.'+suffix+'@qq.com'
            updated_email='codex.updated.'+suffix+'@qq.com'
            with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=10) as client:
                response=client.post('/auth/register',json={'username':member_name,'password':test_password,'qq_email':member_email})
                response.raise_for_status()

            async def fill(selector,value):
                await evaluate("(() => {const field=document.querySelector("+json.dumps(selector)+");field.value="+json.dumps(value)+";field.dispatchEvent(new Event('input',{bubbles:true}))})()")

            await evaluate("document.querySelector('.auth-switch').click()")
            await wait_for("document.querySelector('.register-email') !== null")
            await screenshot('registration-desktop.png')
            await fill('input[autocomplete=username]',owner_name)
            await fill('.register-email',owner_email)
            await evaluate("(() => {const field=document.querySelector('.register-identity');field.value='teacher';field.dispatchEvent(new Event('change',{bubbles:true}))})()")
            await fill('input[autocomplete=new-password]',test_password)
            await fill('.confirm-password',test_password)
            await evaluate("document.querySelector('.auth-form').requestSubmit()")
            await wait_for("document.querySelector('.auth-success')?.textContent.includes('注册成功')")
            assert await evaluate("document.querySelector('input[type=password]').value===''")
            # Test fixture only: confirm this freshly created synthetic teacher;
            # never alter demo accounts or infer any real person's identity.
            subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-m','backend.teacher_accounts','approve',owner_name],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
            await fill('input[autocomplete=current-password]',test_password)
            await evaluate("document.querySelector('.auth-form').requestSubmit()")
            await wait_for("document.querySelector('main[data-page=projects]') && document.querySelector('.team-create-button') && !document.querySelector('[data-nav=projects]:disabled')")
            assert await evaluate("document.querySelectorAll('.project-card').length===0"), 'Registration must not grant project access'
            await evaluate("document.querySelector('.team-create-button').click()")
            await wait_for("document.querySelector('dialog[open] .team-create-form') !== null")
            await fill('.team-create-form input','浏览器合成实验团队-'+suffix)
            await fill('.team-create-form textarea','模拟教师/学生组织测试，不代表真实教师身份或真实团队。')
            await evaluate("document.querySelector('.team-create-form').requestSubmit()")
            await wait_for("document.querySelector('.team-code') !== null && !document.querySelector('header button.primary:disabled')")
            with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=10) as client:
                auth=client.post('/auth/login',json={'username':member_name,'password':test_password});auth.raise_for_status()
                member_headers={'Authorization':'Bearer '+auth.json()['access_token']}
                try:
                    assert client.post('/teams',headers=member_headers,json={'name':'student cannot create'}).status_code==403
                finally:
                    client.post('/auth/logout',headers=member_headers)
            await evaluate("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='退出登录').click()")
            await wait_for("document.querySelector('.login-card input[type=password]') !== null")
            await fill('input[autocomplete=username]',member_name)
            await fill('input[autocomplete=current-password]',test_password)
            await evaluate("document.querySelector('.auth-form').requestSubmit()")
            await wait_for("document.querySelector('.team-apply-button') && !document.querySelector('[data-nav=projects]:disabled')")
            assert await evaluate("!document.querySelector('.team-create-form') && !document.querySelector('.team-library-button')")
            await evaluate("document.querySelector('.team-apply-button').click()")
            await wait_for("document.querySelector('dialog[open] .team-apply-form') !== null")
            await fill('.team-apply-form input',suffix)
            await wait_for("document.querySelectorAll('.team-apply-form select option').length===2 && !document.querySelector('.team-apply-form select').disabled")
            await evaluate("(() => { const select=document.querySelector('.team-apply-form select');select.selectedIndex=1;select.dispatchEvent(new Event('change',{bubbles:true})); })()")
            await wait_for("document.querySelector('.team-directory-description')?.textContent.includes('模拟教师')")
            assert await evaluate("document.querySelector('.team-apply-form select').selectedOptions[0].textContent.includes('1 人')")
            await fill('.team-apply-form textarea','浏览器合成学生申请，非真实人员。')
            await evaluate("document.querySelector('.team-apply-form').requestSubmit()")
            await wait_for("document.querySelector('.own-applications')?.textContent.includes('待审批')")
            await screenshot('team-student-pending.png')
            await evaluate("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='退出登录').click()")
            await wait_for("document.querySelector('.login-card input[type=password]') !== null")
            await fill('input[autocomplete=username]',owner_name)
            await fill('input[autocomplete=current-password]',test_password)
            await evaluate("document.querySelector('.auth-form').requestSubmit()")
            await wait_for("document.querySelector('.team-code') && !document.querySelector('[data-nav=projects]:disabled')")
            await evaluate("Array.from(document.querySelectorAll('.team-workspace button')).find(e=>e.textContent==='刷新团队').click()")
            await wait_for("Array.from(document.querySelectorAll('.team-review button')).some(e=>e.textContent==='批准加入')")
            await screenshot('team-approval-desktop.png')
            await evaluate("Array.from(document.querySelectorAll('.team-review button')).find(e=>e.textContent==='批准加入').click()")
            await wait_for("document.querySelector('#team-confirm-dialog-title') !== null")
            await evaluate("document.querySelector('dialog[open] .actions .primary').click()")
            await wait_for("document.querySelectorAll('.team-person').length===2 && !document.querySelector('dialog[open]')")
            await screenshot('team-members-desktop.png')
            await evaluate("document.querySelector('.profile-button').focus();document.querySelector('.profile-button').click()")
            await wait_for("document.querySelector('dialog[open] .profile-form') !== null")
            assert await evaluate("document.activeElement===document.querySelector('.profile-form input') && document.body.style.overflow==='hidden' && !document.querySelector('main > .profile-form')"), 'Profile should open as a focused modal'
            await fill('.profile-form input','尚未保存的名称')
            await rpc('Input.dispatchKeyEvent', {'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
            await rpc('Input.dispatchKeyEvent', {'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
            await wait_for("document.querySelector('dialog')===null && document.body.style.overflow!== 'hidden'")
            assert await evaluate("document.activeElement===document.querySelector('.profile-button')"), 'Closing modal should restore focus'
            await evaluate("document.querySelector('.profile-button').focus();document.querySelector('.profile-button').click()")
            await wait_for("document.querySelector('dialog[open] .profile-form') !== null")
            assert await evaluate("document.querySelector('.profile-form input').value!=='尚未保存的名称'"), 'Escape must discard unsaved draft'
            await fill('.profile-form input[type=email]',member_email)
            await evaluate("document.querySelector('.profile-form').requestSubmit()")
            await wait_for("document.querySelector('dialog[open] .error') !== null && !document.querySelector('.profile-form fieldset:disabled')")
            assert await evaluate("document.querySelector('.profile-form input[type=email]').value==="+json.dumps(member_email)), 'Failed save should preserve input'
            await screenshot('profile-modal-error.png')
            await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            assert await evaluate("document.documentElement.scrollWidth<=390 && document.querySelector('dialog').getBoundingClientRect().right<=390")
            await screenshot('profile-modal-mobile.png')
            await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
            await fill('.profile-form input','合成教师（测试账号）')
            await fill('.profile-form input[type=email]',updated_email)
            await screenshot('profile-modal-desktop.png')
            await evaluate("document.querySelector('.profile-form').requestSubmit()")
            await wait_for("!document.querySelector('.profile-form') && !document.querySelector('header button.primary:disabled')")
            await evaluate("document.querySelector('header button.primary').click()")
            await fill('.create-form input','账号与通讯录验收-'+suffix)
            await fill('.create-form textarea','自动合成测试项目；邮箱为未验证测试别名，不发送邮件。')
            await evaluate("document.querySelector('.create-form').requestSubmit()")
            await wait_for("document.querySelectorAll('.project-card').length===1 && document.querySelector('.edit-project-button') && !document.querySelector('header button.primary:disabled')")
            await evaluate("document.querySelector('.project-card-edit').click()")
            await wait_for("document.querySelector('dialog[open] .edit-project-form') !== null")
            assert await evaluate("document.activeElement===document.querySelector('.edit-project-form input')"), 'Project edit should focus name field'
            await fill('.edit-project-form input','取消的项目名称')
            await evaluate("document.querySelector('.edit-project-form .ghost').click()")
            await wait_for("document.querySelector('dialog')===null")
            assert await evaluate("!document.querySelector('.project-card h2').textContent.includes('取消的项目名称')"), 'Cancel must not save project'
            await evaluate("document.querySelector('.edit-project-button').click()")
            await wait_for("document.querySelector('dialog[open] .edit-project-form') !== null")
            renamed='已修改的合成项目-'+suffix
            await fill('.edit-project-form input',renamed)
            await fill('.edit-project-form textarea','通过浏览器修改的项目说明，保持成员与项目ID不变。')
            await screenshot('project-modal-desktop.png')
            await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            assert await evaluate('document.documentElement.scrollWidth<=390')
            await screenshot('project-modal-mobile.png')
            await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
            await evaluate("document.querySelector('.edit-project-form').requestSubmit()")
            await wait_for("document.querySelector('.project-card h2')?.textContent==="+json.dumps(renamed)+" && !document.querySelector('.edit-project-form')")
            await evaluate("document.querySelector('.open-member-dialog').click()")
            await wait_for("document.querySelector('dialog[open] .member-form') !== null")
            await fill('.member-form input',member_name)
            await evaluate("document.querySelector('.member-form').requestSubmit()")
            await wait_for("document.querySelectorAll('.members-table tbody tr').length===2 && !document.querySelector('header button.primary:disabled')")
            await screenshot('project-edit-admin.png')
            await evaluate("document.querySelector('[data-nav=runtime]').click()")
            await wait_for("document.querySelectorAll('.center-metrics button').length===2")
            for index in range(2):
                await evaluate(f"document.querySelectorAll('.center-metrics button')[{index}].click()")
                await wait_for("document.querySelector('.task-center select')?.value==='failed'")
                assert await evaluate("document.querySelectorAll('.task-center input[type=date]').length===2")
                await evaluate("document.querySelector('[data-nav=runtime]').click()")
                await wait_for("document.querySelectorAll('.center-metrics button').length===2")
            await evaluate("document.querySelector('[data-nav=documents]').click()")
            await wait_for("document.querySelector('.upload-button') !== null")
            assert await evaluate("document.querySelector('.upload-area')===null")
            await evaluate("document.querySelector('.upload-button').click()")
            await wait_for("document.querySelector('dialog[open] .upload-area input[multiple]') !== null")
            batch_paths=[]
            for tag in ('a','b'):
                file_path=output / f'ui-batch-{tag}-{suffix}.txt'
                file_path.write_bytes(b'   ')
                batch_paths.append(str(file_path))
            tree=await rpc('DOM.getDocument')
            node=await rpc('DOM.querySelector',{'nodeId':tree['root']['nodeId'],'selector':'.upload-area input[type=file]'})
            await rpc('DOM.setFileInputFiles',{'nodeId':node['nodeId'],'files':batch_paths})
            await evaluate("document.querySelector('.upload-area input[type=checkbox]').click();document.querySelector('.upload-area').requestSubmit()")
            await wait_for("document.querySelectorAll('.documents-table tbody tr').length===2 && !document.querySelector('dialog[open]')")
            assert await evaluate("document.querySelector('.documents-table tbody').textContent.includes('合成教师')")
            await fill('.file-search input','batch-a')
            await evaluate("document.querySelector('.file-search').requestSubmit()")
            await wait_for("document.querySelectorAll('.documents-table tbody tr').length===1")
            await evaluate("document.querySelector('.doc-actions .text-button').click()")
            await wait_for("document.querySelector('dialog[open] .version-row button') !== null")
            await evaluate("window.__realOpen=window.open;window.open=(...args)=>window.__sourceTab=window.__realOpen(...args)")
            await evaluate("document.querySelector('.version-row button').click()", user_gesture=True)
            await wait_for("!!window.__sourceTab?.document.querySelector('main pre') && !!window.__sourceTab.document.querySelector('a[download]')")
            await evaluate("window.__sourceTab.close();window.open=window.__realOpen;delete window.__sourceTab;delete window.__realOpen;document.querySelector('dialog[open] .dialog-close').click()")
            await wait_for("!document.querySelector('dialog[open]')")
            for action, condition in [('撤下', "document.querySelector('.documents-table tbody')?.textContent.includes('已停止访问')"),
                                      ('恢复访问', "document.querySelector('.documents-table tbody')?.textContent.includes('撤下') && !document.querySelector('.documents-table tbody')?.textContent.includes('已停止访问')"),
                                      ('撤下', "document.querySelector('.documents-table tbody')?.textContent.includes('已停止访问')"),
                                      ('删除', "document.querySelectorAll('.documents-table tbody tr').length===0")]:
                await evaluate("Array.from(document.querySelectorAll('.doc-actions button')).find(b=>b.textContent==="+json.dumps(action)+").click()")
                await wait_for("document.querySelector('#document-action-dialog-title') !== null")
                await evaluate("document.querySelector('dialog[open] .actions .primary').click()")
                await wait_for("!document.querySelector('dialog[open]') && ("+condition+")")
            await evaluate("document.querySelector('[data-nav=projects]').click()")
            await wait_for("document.querySelector('.project-card') !== null && !document.querySelector('[data-nav=projects]:disabled')")
            print('PASS team directory, project modals, batch upload, filename search, source tab, restore/delete, task links and date controls')
            await evaluate("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='退出登录').click()")
            await wait_for("document.querySelector('.login-card input[type=password]') !== null")
            await fill('input[autocomplete=username]',member_name)
            await fill('input[autocomplete=current-password]',test_password)
            await evaluate("document.querySelector('.auth-form').requestSubmit()")
            await wait_for("document.querySelectorAll('.members-table tbody tr').length===2 && !document.querySelector('header button.primary:disabled')")
            assert await evaluate("!document.querySelector('.open-member-dialog') && !document.querySelector('.edit-project-button') && !document.querySelector('.project-card-edit') && !document.querySelector('.members-table .danger-link')"), 'Member must only view contacts'
            assert await evaluate("Array.from(document.querySelectorAll('.members-table a')).some(e=>e.getAttribute('href')==="+json.dumps('mailto:'+updated_email)+")"), 'Shared QQ mailto contact missing'
            await wait_for("document.querySelectorAll('.team-person').length===2")
            assert await evaluate("document.querySelector('.team-create-button')===null"), 'Students must not get a team creation form'
            assert await evaluate("document.querySelector('.delete-team-button')===null"), 'Only the team owner may see the deletion entry'
            await evaluate("document.querySelector('.team-library-button').click()")
            await wait_for("document.querySelector('#document-library') && !document.querySelector('[data-nav=projects]:disabled')")
            assert await evaluate("document.querySelector('.project-selector').selectedOptions[0].textContent.includes('全团队公共') && !document.querySelector('.upload-button')"), 'Student public library must be read only'
            await screenshot('team-public-library.png')
            await evaluate("document.querySelector('[data-nav=projects]').click()")
            await wait_for("document.querySelector('.project-card') && !document.querySelector('[data-nav=projects]:disabled')")
            await evaluate("document.querySelector('.project-card').click()")
            await wait_for("document.querySelectorAll('.members-table tbody tr').length===2")
            await screenshot('member-contacts-desktop.png')
            await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
            assert await evaluate('document.documentElement.scrollWidth <= 390'), 'Member contacts mobile overflow'
            await screenshot('member-contacts-mobile.png')
            await evaluate("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='退出登录').click()")
            await wait_for("document.querySelector('.login-card input[type=password]') !== null")
            await evaluate("document.querySelector('.auth-switch').click()")
            await wait_for("document.querySelector('.register-email') !== null")
            await screenshot('registration-mobile.png')
            assert await evaluate('document.documentElement.scrollWidth <= 390'), 'Registration mobile overflow'
            await evaluate("document.querySelector('.auth-switch').click()")
            await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
            if '--delete-team' in sys.argv:
                await fill('input[autocomplete=username]',owner_name)
                await fill('input[autocomplete=current-password]',test_password)
                await evaluate("document.querySelector('.auth-form').requestSubmit()")
                await wait_for("document.querySelector('.delete-team-button') && !document.querySelector('[data-nav=projects]:disabled')")
                team_name='浏览器合成实验团队-'+suffix
                await evaluate("document.querySelector('.delete-team-button').click()")
                await wait_for("document.querySelector('.delete-team-form') !== null")
                await fill('.delete-team-name','not-the-team-name')
                assert await evaluate("document.querySelector('.confirm-delete-team').disabled"), 'Deletion must require the exact name'
                await screenshot('delete-team-desktop.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert await evaluate('document.documentElement.scrollWidth <= 390'), 'Team deletion mobile overflow'
                await screenshot('delete-team-mobile.png')
                await fill('.delete-team-name',team_name)
                assert await evaluate("!document.querySelector('.confirm-delete-team').disabled")
                await evaluate("document.querySelector('.delete-team-form').requestSubmit()")
                await wait_for("document.querySelector('.team-notice')?.textContent.includes('已删除') && !document.querySelector('.team-overview') && !document.querySelector('[data-nav=projects]:disabled')")
                assert await evaluate("document.querySelectorAll('.project-card').length===0"), 'Deleted team projects must disappear'
                with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=10) as client:
                    auth=client.post('/auth/login',json={'username':member_name,'password':test_password});auth.raise_for_status()
                    headers={'Authorization':'Bearer '+auth.json()['access_token']}
                    try:
                        assert client.get('/teams',headers=headers).json()==[]
                        assert client.get('/projects',headers=headers).json()==[]
                        assert client.get('/team-applications',headers=headers).json()[0]['team_deleted']
                    finally:
                        client.post('/auth/logout',headers=headers)
                await evaluate("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='退出登录').click()")
                await wait_for("document.querySelector('.login-card input[type=password]') !== null")
                await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                print('PASS owner-only team deletion, exact-name confirmation, desktop/mobile, project removal and member access revocation; deleted only the team created by this run')
            print('PASS synthetic teacher confirmation, teacher-only creation, student application/approval, public/private project roles, registration, contacts and mobile layout')
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
                    expected_projects = project_response.json()
                finally:
                    api_client.post('/auth/logout',headers=api_headers)
            await evaluate("(() => {const u=document.querySelector('input[autocomplete=username]');const p=document.querySelector('input[type=password]');u.value=" + json.dumps(username) + ";u.dispatchEvent(new Event('input',{bubbles:true}));p.value=" + json.dumps(account['password']) + ";p.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('form').requestSubmit();return true})()")
            await wait_for("document.querySelector('h1')?.textContent === '团队与项目' && document.querySelectorAll('.project-card').length > 0 && !document.querySelector('header button.primary:disabled')")
            active_team=await evaluate("document.querySelector('.team-selector').value")
            expected=[p['name'] for p in expected_projects if p['team_id']==active_team]
            names = await evaluate("Array.from(document.querySelectorAll('.project-card h2')).map(e=>e.textContent)")
            assert names == expected, 'UI project scope differs from server-authorized projects'
            if username == 'demo_member':
                assert await evaluate("document.querySelector('.open-member-dialog') === null"), 'Member management exposed'
            await screenshot(username + '.png')
            assert await evaluate("document.querySelectorAll('[data-nav]').length===6 && document.querySelector('.sidebar-emblem')?.naturalWidth>0"), 'Six pages or university emblem missing'

            async def select_project(project_name):
                target_project=next(p for p in expected_projects if p['name']==project_name)
                await evaluate("(() => {const field=document.querySelector('.team-selector');field.value="+json.dumps(target_project['team_id'])+";field.dispatchEvent(new Event('change',{bubbles:true}))})()")
                await wait_for("Array.from(document.querySelectorAll('.project-card h2')).some(e=>e.textContent==="+json.dumps(project_name)+") && !document.querySelector('[data-nav=projects]:disabled')")
                await evaluate("Array.from(document.querySelectorAll('.project-card')).find(e=>e.querySelector('h2').textContent==="+json.dumps(project_name)+").click()")

            if username == 'demo_admin' and '--qa-ui' in sys.argv:
                # Browser-only synthetic responses: no model call or DB record.
                await evaluate("""(() => {
                    window.__qaOriginalFetch = window.fetch;
                    const notice = '生成内容未通过结构、引用或原文摘录校验，已停止展示该草稿，请查看证据或重新提问。';
                    window.__qaReply = {id:'synthetic-ui',question:'合成问题：困难学生有哪些帮助途径？',
                      created_at:new Date().toISOString(),status:'answered',answer:'可以申请临时困难补助。 [E1]',
                      claims:[{text:'可以申请临时困难补助。',evidence_ids:['E1']}],
                      citations:[{evidence_id:'E1',quote:'符合条件的困难学生可以申请临时困难补助。',claim_index:0}],
                      evidence:[{evidence_id:'E1',chunk_id:'synthetic-chunk',title:'合成资料（界面验证）',version_number:1,page:1}],
                      details:{warnings:[],retrieval:{effective_mode:'hybrid',rerank_status:'applied'},generation_ms:100,total_ms:150}};
                    window.__qaFailure = {...window.__qaReply,status:'invalid_citations',answer:notice,
                      claims:[],citations:[],details:{...window.__qaReply.details,warnings:[{code:'CITATION_VALIDATION_FAILED',message:notice}]}};
                    window.fetch = async (url,options) => {
                      const parsed = new URL(url,location.href);
                      if (parsed.pathname === '/api/questions' || parsed.pathname.startsWith('/api/questions/')) {
                        const payload = parsed.pathname === '/api/questions' && options?.method !== 'POST' ? [] : window.__qaReply;
                        return new Response(JSON.stringify(payload),{status:200,headers:{'Content-Type':'application/json'}});
                      }
                      return window.__qaOriginalFetch(url,options);
                    };
                })()""")
                await evaluate("document.querySelector('[data-nav=questions]').click()")
                await wait_for("document.querySelector('.question-input') && !document.querySelector('.question-history .ghost:disabled')")
                await evaluate("(() => {const field=document.querySelector('.question-input');field.value='合成问题：困难学生有哪些帮助途径？';field.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.qa-consent input').click();document.querySelector('.question-form').requestSubmit()})()")
                await wait_for("document.querySelector('[data-status=answered] .answer-claims')?.textContent.includes('临时困难补助')")
                assert await evaluate("document.querySelectorAll('.answer-claims .citation-button').length===1")
                await screenshot('qa-ui-answer.png')
                await evaluate("window.__qaReply=window.__qaFailure;document.querySelector('.question-form').requestSubmit()")
                await wait_for("document.querySelector('[data-status=invalid_citations] .qa-notice') !== null")
                assert await evaluate("document.querySelectorAll('.qa-notice').length===1 && document.querySelectorAll('.qa-warning').length===0 && !document.querySelector('.answer-claims') && !document.querySelector('.verification-note')"), 'Rejected answer or duplicate warning displayed'
                await screenshot('qa-ui-failure.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert await evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                await screenshot('qa-ui-failure-mobile.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                await evaluate("document.querySelector('[data-nav=projects]').click()")
                await wait_for("!document.querySelector('.question-panel') && document.querySelector('.project-card') !== null")
                await evaluate("window.fetch=window.__qaOriginalFetch;delete window.__qaOriginalFetch;delete window.__qaReply;delete window.__qaFailure")
                print('PASS synthetic QA rendering, citations, duplicate warning removal and mobile layout; no model call')
                await wait_for("!document.querySelector('[data-nav=projects]:disabled')")
            if username == 'demo_admin' and '--centers' in sys.argv:
                report=json.loads((ROOT/'.local/centers-live-check.json').read_text(encoding='utf-8'))
                await select_project(report['project_name'])
                await wait_for("!document.querySelector('[data-nav=documents]:disabled')")
                await evaluate("document.querySelector('[data-nav=documents]').click()")
                await wait_for("document.querySelector('.upload-button') !== null && document.querySelectorAll('.documents-table tbody tr').length===2")
                await evaluate("document.querySelector('.upload-button').click()")
                await wait_for("document.querySelector('.upload-area input[type=file]') !== null")
                upload_file=ROOT/'.local/centers-check/synthetic-encrypted.pdf'
                tree=await rpc('DOM.getDocument')
                node=await rpc('DOM.querySelector',{'nodeId':tree['root']['nodeId'],'selector':'.upload-area input[type=file]'})
                await rpc('DOM.setFileInputFiles',{'nodeId':node['nodeId'],'files':[str(upload_file)]})
                await wait_for("document.querySelector('.pdf-password') !== null")
                await evaluate("(() => {const p=document.querySelector('.pdf-password');p.value='deliberately-wrong';p.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.upload-area input[type=checkbox]').click();document.querySelector('.upload-area').requestSubmit()})()")
                await wait_for("document.querySelector('.upload-area .error')?.textContent.includes('密码错误')",attempts=150)
                assert await evaluate("document.querySelector('.pdf-password').value===''")
                await asyncio.sleep(3.4)
                assert await evaluate("document.querySelector('.upload-area .error')?.textContent.includes('密码错误')"), 'Polling must not erase upload feedback'
                await screenshot('pdf-upload-desktop.png')
                await evaluate("document.querySelector('.upload-area .ghost').click()")
                for name,selector in [('tasks','.task-center'),('runtime','.runtime-center'),('search','#evidence-search'),('questions','.question-panel')]:
                    await evaluate("document.querySelector('[data-nav="+name+"]').click()")
                    await wait_for("document.querySelector("+json.dumps(selector)+") !== null")
                    await wait_for("!document.querySelector('[data-nav="+name+"]:disabled')")
                    assert await evaluate("document.querySelector('main').dataset.page==="+json.dumps(name)+" && document.querySelector('.project-selector').value==="+json.dumps(report['project_id']))
                    if name=='tasks':
                        await wait_for("document.querySelectorAll('.task-center tbody tr').length===2")
                    if name=='runtime':
                        await wait_for("document.querySelectorAll('.audit-table tbody tr').length>=4")
                    if name in {'tasks','runtime'}:
                        await evaluate('window.scrollTo(0,0)')
                        await screenshot('seven-'+name+'-desktop.png')
                        await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                        assert await evaluate('document.documentElement.scrollWidth <= 390'), 'Mobile overflow: '+name
                        await screenshot('seven-'+name+'-mobile.png')
                        await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                await evaluate("document.querySelector('[data-nav=projects]').click()")
                await wait_for("document.querySelector('.project-card') !== null && !document.querySelector('[data-nav=projects]:disabled')")
                print('PASS six isolated pages, university emblem, password failure clearing, task/audit data and mobile widths')
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
                await wait_for("document.querySelector('.upload-button') !== null")
                previous_version = await evaluate("(() => {const row=Array.from(document.querySelectorAll('.documents-table tbody tr')).find(e=>e.querySelector('strong')?.textContent==='browser-upload.md');if(row){const n=Number(row.querySelector('.badge').textContent.match(/v(\\d+)/)?.[1] || 0);Array.from(row.querySelectorAll('button')).find(e=>e.textContent==='更新').click();return n}document.querySelector('.upload-button').click();return 0})()")
                await wait_for("document.querySelector('.upload-area input[type=file]') !== null")
                tree = await rpc('DOM.getDocument')
                node = await rpc('DOM.querySelector',{'nodeId':tree['root']['nodeId'],'selector':'.upload-area input[type=file]'})
                await rpc('DOM.setFileInputFiles',{'nodeId':node['nodeId'],'files':[str(upload_file)]})
                await evaluate("document.querySelector('.upload-area input[type=checkbox]').click()")
                await evaluate("document.querySelector('.upload-area').requestSubmit()")
                for attempt in range(120):
                    state = await evaluate("(() => {const row=Array.from(document.querySelectorAll('.documents-table tbody tr')).find(e=>e.querySelector('strong')?.textContent==='browser-upload.md');return !!row && row.querySelector('.badge').textContent.includes('v"+str(previous_version+1)+" · 已就绪') && !document.querySelector('.upload-area')})()")
                    if state:
                        break
                    await asyncio.sleep(.5)
                else:
                    raise RuntimeError('Browser upload did not become ready')
                await evaluate("(() => {const row=Array.from(document.querySelectorAll('.documents-table tbody tr')).find(e=>e.querySelector('strong')?.textContent==='browser-upload.md');Array.from(row.querySelectorAll('button')).find(e=>e.textContent==='版本与原文').click()})()")
                await wait_for("document.querySelector('.version-panel .version-row') !== null")
                await evaluate("window.__realOpen=window.open;window.open=(...args)=>window.__sourceTab=window.__realOpen(...args)")
                await evaluate("document.querySelector('.version-panel .version-row button').click()", user_gesture=True)
                await wait_for("!!window.__sourceTab?.document.querySelector('main pre')")
                await evaluate("window.__sourceTab.close();window.open=window.__realOpen;delete window.__sourceTab;delete window.__realOpen")
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
                await select_project(search_report['project_name'])
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
                await evaluate("window.__realOpen=window.open;window.open=(...args)=>window.__sourceTab=window.__realOpen(...args)")
                await evaluate("document.querySelector('.evidence-card .source-button').click()", user_gesture=True)
                await wait_for("!!window.__sourceTab?.document.querySelector('main pre')")
                await evaluate("window.__sourceTab.close();window.open=window.__realOpen;delete window.__sourceTab;delete window.__realOpen")
                await screenshot('search-hybrid.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                assert await evaluate('window.innerWidth === 390 && document.documentElement.scrollWidth <= 390'), 'Mobile search page overflows'
                await screenshot('search-mobile.png')
                await rpc('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                print('PASS browser local keyword, real hybrid rerank, source tab and mobile search width')
            if username == 'demo_admin' and '--questions' in sys.argv:
                report = json.loads((ROOT / '.local/qa-live-check.json').read_text(encoding='utf-8'))
                await evaluate("document.querySelector('[data-nav=projects]').click()")
                await wait_for("document.querySelector('.project-card') !== null && !document.querySelector('[data-nav=projects]:disabled')")
                await select_project(report['project_name'])
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
                await evaluate("window.__realOpen=window.open;window.open=(...args)=>window.__sourceTab=window.__realOpen(...args)")
                await evaluate("document.querySelector('.answer-claims .citation-button').click()", user_gesture=True)
                await wait_for("!!window.__sourceTab?.document.querySelector('main pre')")
                await evaluate("window.__sourceTab.close();window.open=window.__realOpen;delete window.__sourceTab;delete window.__realOpen")
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
        (output / 'result.json').write_text(json.dumps({'status': 'passed', 'users_checked': list(accounts), 'ingestion_checked':'--ingestion' in sys.argv, 'search_checked':'--search' in sys.argv, 'questions_checked':'--questions' in sys.argv, 'synthetic_qa_ui_checked':'--qa-ui' in sys.argv, 'runtime_checked':'--runtime' in sys.argv,'centers_checked':'--centers' in sys.argv,'accounts_checked':'--accounts' in sys.argv,'team_deletion_checked':'--delete-team' in sys.argv, 'checked_at': time.time()}, indent=2), encoding='utf-8')
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
