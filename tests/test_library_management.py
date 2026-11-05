from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile

import pytest

from backend.app.ingestion import parse_and_chunk
from backend.app.models import utcnow
from tests.conftest import login
from tests.test_documents import upload, worker_for


def package(parts):
    result = BytesIO()
    with ZipFile(result, 'w') as archive:
        for path, value in parts.items():
            archive.writestr(path, value)
    return result.getvalue()


OFFICE = {
    '.docx': {'word/document.xml': '<document><p><r><t>Project membership</t></r></p></document>'},
    '.xlsx': {
        'xl/workbook.xml': '<workbook xmlns:r="urn:rel"><sheet name="Budget" r:id="r1"/></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml': '<worksheet><row r="7"><c r="B7" t="s"><v>0</v></c></row></worksheet>',
        'xl/sharedStrings.xml': '<sst><si><r><t>Project </t></r><r><t>membership</t></r></si></sst>',
    },
    '.pptx': {
        'ppt/presentation.xml': '<presentation xmlns:r="urn:rel"><sldId r:id="r1"/></presentation>',
        'ppt/_rels/presentation.xml.rels': '<Relationships><Relationship Id="r1" Target="slides/slide3.xml"/></Relationships>',
        'ppt/slides/slide3.xml': '<sld><p><r><t>Project membership</t></r></p></sld>',
    },
}


@pytest.mark.parametrize('suffix', OFFICE)
def test_office_text_and_location(suffix):
    chunks = parse_and_chunk(package(OFFICE[suffix]), suffix)
    assert 'Project membership' in chunks[0].text
    if suffix == '.xlsx':
        assert (chunks[0].section, chunks[0].line_start) == ('Budget', 7)
    elif suffix == '.pptx':
        assert chunks[0].section == '第 1 张幻灯片'


def test_document_lifecycle_and_search_scope(environment):
    client, engine, ids = environment
    owner, member = login(client), login(client, 'member')
    name = 'Membership_Handbook.docx'
    content = package(OFFICE['.docx'])
    result = upload(client, ids, owner, name=name, text=content).json()
    url = '/api/documents/' + result['document_id']
    source = url + '/source?version_id=' + result['version_id']
    listing = '/api/projects/' + ids['project'] + '/documents'
    assert client.delete(url, headers=owner).status_code == 409
    assert client.post(url + '/withdraw', headers=owner).status_code == 200
    assert client.get(source, headers=member).status_code == 404
    assert client.get(listing, headers=member).json() == []
    assert client.post(url + '/restore', headers=member).status_code == 403
    assert client.post(url + '/restore', headers=owner).status_code == 200
    worker_for(client, engine).run_once()
    found = client.get(listing, headers=member, params={'q': 'ship_hand'}).json()
    assert len(found) == 1 and found[0]['latest_version']['uploader'] == 'owner'
    assert found[0]['latest_version']['status'] == 'ready'
    assert client.get(listing, headers=member, params={'q': '%'}).json() == []
    assert client.get(source, headers=member).content == content

    search = client.post('/api/search', headers=member, json={
        'project_id': ids['project'], 'mode': 'keyword', 'query': 'membership'
    })
    assert search.status_code == 200 and search.json()['results']
    history = '/api/projects/' + ids['project'] + '/search-history'
    assert client.get(history, headers=owner).json() == []
    assert client.get(history, headers=member).json()[0]['query'] == 'membership'
    assert client.get(history, headers=login(client, 'outsider')).status_code == 404

    assert client.post(url + '/withdraw', headers=owner).status_code == 200
    assert client.post(url + '/restore', headers=owner).status_code == 200
    assert client.get(source, headers=member).status_code == 200
    assert client.post(url + '/withdraw', headers=owner).status_code == 200
    assert client.delete(url, headers=owner).status_code == 204
    assert client.get(listing, headers=owner).json() == []
    assert client.get(source, headers=owner).status_code == 404
    assert client.post(url + '/restore', headers=owner).status_code == 404


def test_directory_fuzzy_selection_and_task_date_bounds(environment):
    client, _, ids = environment
    owner, outsider = login(client), login(client, 'outsider')
    result = client.get('/api/team-directory', headers=outsider, params={'q': 'tecte'}).json()
    assert len(result) == 1 and result[0]['name'] == 'Protected team'
    assert result[0]['member_count'] == 2 and 'code' not in result[0]
    response = client.post('/api/team-applications', headers=outsider, json={'team_id': result[0]['id']})
    assert response.status_code == 201
    assert client.get('/api/team-directory', headers=outsider, params={'q': 'tecte'}).json() == []

    result = upload(client, ids, owner).json()
    path = '/api/projects/' + ids['project'] + '/tasks'
    now = utcnow()
    included = client.get(path, headers=owner, params={
        'created_from': (now - timedelta(days=1)).isoformat(),
        'created_before': (now + timedelta(days=1)).isoformat(),
    }).json()
    assert included[0]['id'] == result['task_id']
    assert client.get(path, headers=owner, params={'created_from': (now + timedelta(days=1)).isoformat()}).json() == []
