"""Extract text from bounded, local Office Open XML packages."""
import posixpath
import re
from xml.etree import ElementTree
from zipfile import ZipFile


def _tag(element):
    return element.tag.rsplit('}', 1)[-1]


def _named(node, name):
    return (element for element in node.iter() if _tag(element) == name)


def _text(node):
    return ''.join(element.text or '' for element in _named(node, 't'))


def office_sections(source, suffix, max_chars):
    with ZipFile(source) as package:
        entries = package.infolist()
        if len(entries) > 10_000 or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
            raise ValueError('Office package exceeds expansion limit')

        def xml(name):
            with package.open(name) as part:
                data = part.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024 or b'<!DOCTYPE' in data.upper() or b'\x00' in data:
                raise ValueError('Office XML exceeds limits or is not supported')
            return ElementTree.fromstring(data)

        def targets(folder, main, item):
            rels = xml(f'{folder}/_rels/{main}.rels')
            by_id = {}
            for rel in rels:
                if rel.get('TargetMode') == 'External':
                    continue
                target = rel.get('Target', '')
                path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else f'{folder}/{target}')
                if path.startswith(folder + '/'):
                    by_id[rel.get('Id')] = path
            for index, element in enumerate(_named(xml(f'{folder}/{main}'), item), 1):
                ref = next((value for key, value in element.attrib.items() if key.endswith('}id')), None)
                if ref in by_id:
                    yield index, element.get('name'), by_id[ref]

        if suffix == '.docx':
            for number, paragraph in enumerate(_named(xml('word/document.xml'), 'p'), 1):
                yield '正文', number, _text(paragraph)
        elif suffix == '.pptx':
            for index, _, path in targets('ppt', 'presentation.xml', 'sldId'):
                slide = xml(path)
                for number, paragraph in enumerate(_named(slide, 'p'), 1):
                    yield f'第 {index} 张幻灯片', number, _text(paragraph)
        else:
            shared = []
            if 'xl/sharedStrings.xml' in package.namelist():
                shared = [_text(item) for item in _named(xml('xl/sharedStrings.xml'), 'si')]
            for index, title, path in targets('xl', 'workbook.xml', 'sheet'):
                for number, row in enumerate(_named(xml(path), 'row'), 1):
                    cells = []
                    row_length = 0
                    for cell in _named(row, 'c'):
                        value = next((element.text or '' for element in _named(cell, 'v')), '')
                        kind = cell.get('t')
                        if kind == 's' and value:
                            value = shared[int(value)]
                        elif kind == 'inlineStr':
                            value = _text(cell)
                        if value:
                            cell_ref = cell.get('r', '')
                            value = f'{cell_ref}: {value}' if re.fullmatch(r'[A-Z]+[0-9]+', cell_ref) else value
                            row_length += len(value) + 3
                            if row_length > max_chars:
                                raise ValueError('Office row exceeds text limit')
                            cells.append(value)
                    row_number = row.get('r', '')
                    yield title or f'工作表 {index}', int(row_number) if row_number.isdecimal() and len(row_number) <= 9 else number, ' | '.join(cells)
