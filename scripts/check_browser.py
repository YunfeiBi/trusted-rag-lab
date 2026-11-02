"""Local Chrome smoke checks; credentials stay in memory, screenshots have no passwords."""
import asyncio
import base64
import json
import subprocess
from pathlib import Path
import time
import uuid

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

        async def wait_for(expression):
            for attempt in range(60):
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
        await screenshot('login.png')
        records = json.loads((ROOT / '.local' / 'demo_accounts.json').read_text(encoding='utf-8'))
        accounts = {a['username']: a for a in records['accounts']}
        for username in ['demo_admin', 'demo_member', 'demo_other']:
            account = accounts[username]
            await evaluate("(() => {const u=document.querySelector('input[autocomplete=username]');const p=document.querySelector('input[type=password]');u.value=" + json.dumps(username) + ";u.dispatchEvent(new Event('input',{bubbles:true}));p.value=" + json.dumps(account['password']) + ";p.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('form').requestSubmit();return true})()")
            await wait_for("document.querySelector('h1')?.textContent === '我的项目' && document.querySelectorAll('.project-card').length > 0 && !document.querySelector('button.primary:disabled')")
            names = await evaluate("Array.from(document.querySelectorAll('.project-card h2')).map(e=>e.textContent)")
            expected = '独立项目 B' if username == 'demo_other' else '科研项目 A'
            assert names == [expected], 'Unexpected project scope'
            if username == 'demo_member':
                assert await evaluate("document.querySelector('.member-form') === null"), 'Member management exposed'
            await screenshot(username + '.png')
            await evaluate("Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='退出登录').click()")
            await wait_for("document.querySelector('input[type=password]') !== null")
            print('PASS browser login, project scope and logout: ' + username)
        await rpc('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
        await screenshot('login-mobile.png')
        assert await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
        print('PASS mobile login layout width; screenshots in .local/browser-check')
        (output / 'result.json').write_text(json.dumps({'status': 'passed', 'users_checked': list(accounts), 'checked_at': time.time()}, indent=2), encoding='utf-8')
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
