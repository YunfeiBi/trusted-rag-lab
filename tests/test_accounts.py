from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import User
from backend.app.security import verify_password
from tests.conftest import login


def register(client,**changes):
    return client.post('/api/auth/register',json={'username':'中文成员甲','qq_email':'synthetic.user@qq.com','password':'test-password-only',**changes})


def test_chinese_registration_login_hashing_and_no_project_grant(environment):
    client,engine,_=environment
    result=register(client,username=' 中文成员甲 ')
    assert result.status_code==201,result.text
    assert result.json()['username']=='中文成员甲' and result.json()['qq_email']=='synthetic.user@qq.com'
    assert 'test-password-only' not in result.text and 'password_hash' not in result.text
    headers=login(client,'中文成员甲')
    assert client.get('/api/projects',headers=headers).json()==[]
    with Session(engine) as db:
        row=db.scalar(select(User).where(User.username=='中文成员甲'))
        assert row.password_hash!='test-password-only' and verify_password('test-password-only',row.password_hash)


@pytest.mark.parametrize('changes',[
    {'username':'名字 空格'},{'username':'😀'},{'username':42},{'password':'short'},
    {'qq_email':'bad@example.com'},{'qq_email':'bad\r\nBcc:someone@qq.com'},{'role':'admin'},
])
def test_registration_validation_has_no_password_echo(environment,changes):
    client,_,_=environment
    result=register(client,**changes)
    assert result.status_code==422
    assert 'test-password-only' not in result.text and 'short' not in result.text


def test_normalization_and_concurrent_duplicate_registration(environment):
    client,_,_=environment
    assert register(client,username='Ａlice',qq_email='Synthetic.Normal@QQ.COM').status_code==201
    assert register(client,username='Alice',qq_email='another@qq.com').status_code==409
    assert register(client,username='别人',qq_email='synthetic.normal@qq.com').status_code==409
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:register(client),range(2)))
    assert sorted(r.status_code for r in responses)==[201,409]


def test_contact_visibility_membership_and_own_profile(environment):
    client,_,ids=environment
    owner,member,outsider=login(client),login(client,'member'),login(client,'outsider')
    assert client.patch('/api/auth/me',headers=member,json={'display_name':'中文成员乙','qq_email':'synthetic.member@qq.com'}).status_code==200
    path='/api/projects/'+ids['project']+'/members'
    rows=client.get(path,headers=owner).json()
    contact=next(r for r in rows if r['user']['username']=='member')
    assert contact['user']['qq_email']=='synthetic.member@qq.com'
    assert len(client.get(path,headers=member).json())==2
    assert client.get(path,headers=outsider).status_code==404
    assert client.get(path).status_code==401
    # An admin of a different project may discover a username, not contact details.
    client.post('/api/projects',headers=outsider,json={'name':'Other project'})
    found=client.get('/api/users?username=member',headers=outsider)
    assert found.status_code==200 and 'qq_email' not in found.text
    assert client.patch('/api/auth/me',headers=outsider,json={'display_name':'其他人','qq_email':'synthetic.member@qq.com'}).status_code==409
    assert client.get('/api/auth/me',headers=outsider).json()['display_name']=='outsider'
    assert client.patch('/api/auth/me',headers=member,json={'display_name':'冒用','qq_email':'second@qq.com','id':ids['owner']}).status_code==422
    assert client.delete(path+'/'+ids['member'],headers=owner).status_code==204
    assert client.get(path,headers=member).status_code==404


def test_admin_project_edit_and_audit(environment):
    client,_,ids=environment
    owner,member,outsider=login(client),login(client,'member'),login(client,'outsider')
    path='/api/projects/'+ids['project']
    payload={'name':' 修改后的项目 ','description':' 项目新说明 '}
    assert client.patch(path,headers=member,json=payload).status_code==403
    assert client.patch(path,headers=outsider,json=payload).status_code==404
    assert client.patch(path,headers=owner,json={'name':'  '}).status_code==422
    result=client.patch(path,headers=owner,json=payload)
    assert result.status_code==200 and result.json()['name']=='修改后的项目'
    assert client.get(path,headers=member).json()['description']=='项目新说明'
    renamed=client.patch(path,headers=owner,json={'name':'只修改名称'})
    assert renamed.json()['description']=='项目新说明'
    assert client.patch(path,headers=owner,json={'name':None}).status_code==422
    assert client.patch(path,headers=owner,json={}).status_code==422
    events=client.get(path+'/audit-events',headers=owner).json()
    assert events[0]['action']=='project.updated' and '项目新说明' not in str(events)
    assert len(client.get(path+'/members',headers=owner).json())==2
