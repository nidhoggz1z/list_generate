"""Read an immutable Excel phrase library with only the standard library."""
import asyncio
import hashlib
import io
import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree as E
from zipfile import ZipFile, BadZipFile

FILENAME = '话术库对应材料.xlsx'
SHEET_NAME = '审批决议话术库对应材料'
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
HEADER = ['模块话术序号', '标准话术', '对应的放款影像材料']


class PhraseLibraryError(Exception):
    """The required supplemental source cannot be read completely."""


def _check(cancel_check):
    if cancel_check():
        raise asyncio.CancelledError()


def _materials(text, row):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result, role, group = [], '', ''
    for index, line in enumerate(lines):
        if line.endswith(('：', ':')):
            role, group = line, ''
            continue
        numbered = re.fullmatch(r'(\d+(?:\.\d+)+)\s*(.+)', line)
        child = re.fullmatch(r'[（(](\d+)[）)]\s*(.+)', line)
        if not numbered and not child:
            raise ValueError('Unrecognized material line')
        if numbered and index + 1 < len(lines) and re.match(r'^[（(]\d+[）)]', lines[index + 1]):
            group = numbered[2]
            continue
        if numbered:
            group = ''
        name = (numbered or child)[2].strip()
        if not name:
            raise ValueError()
        result.append(dict(materialKey=f'row:{row}/item:{len(result) + 1}', name=name,
            context='\n'.join(p for p in (role, group, line) if p),
            sourceRuleId=f'话术库:行{row}:材料{len(result) + 1}'))
    if not result:
        raise ValueError()
    return result


def snapshot_phrase_library(root, cancel_check=lambda: False):
    """Read current bytes once, bracketed by file identity checks; no stale cache.

    Blank formatting rows are ignored; duplicate display IDs remain separate.
    Formula cells and merged material rows are rejected instead of guessing.
    """
    _check(cancel_check)
    try:
        directory = Path(root).resolve()
        path = directory / FILENAME
        if not path.resolve().is_relative_to(directory):
            raise ValueError()
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if ((before.st_mtime_ns, before.st_ctime_ns, before.st_size, before.st_ino)
                != (after.st_mtime_ns, after.st_ctime_ns, after.st_size, after.st_ino)
                or len(raw) != before.st_size):
            raise ValueError()
        _check(cancel_check)
        with ZipFile(io.BytesIO(raw)) as archive:
            workbook = E.fromstring(archive.read('xl/workbook.xml'))
            sheets = [s for s in workbook.findall(S + 'sheets/' + S + 'sheet') if s.get('name') == SHEET_NAME]
            if len(sheets) != 1:
                raise ValueError()
            relations = E.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
            links = [r for r in relations if r.get('Id') == sheets[0].get(R + 'id')]
            if len(links) != 1 or links[0].get('TargetMode') == 'External':
                raise ValueError()
            target = links[0].attrib['Target']
            target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            if not target.startswith('xl/worksheets/') or '\\' in target:
                raise ValueError()
            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared = [''.join(t.text or '' for t in item.iter(S + 't'))
                          for item in E.fromstring(archive.read('xl/sharedStrings.xml')).findall(S + 'si')]
            worksheet = E.fromstring(archive.read(target))
            if worksheet.findall(S + 'mergeCells/' + S + 'mergeCell'):
                raise ValueError()
            rows, seen_rows = [], set()
            for row in worksheet.findall(S + 'sheetData/' + S + 'row'):
                _check(cancel_check)
                number = int(row.attrib['r'])
                if number < 1 or number in seen_rows:
                    raise ValueError()
                seen_rows.add(number)
                values, seen_cells = {}, set()
                for cell in row.findall(S + 'c'):
                    ref = cell.attrib['r']
                    if ref in seen_cells or not re.fullmatch(r'[A-Z]+' + str(number), ref):
                        raise ValueError()
                    seen_cells.add(ref)
                    if cell.find(S + 'f') is not None:
                        raise ValueError()
                    value = cell.find(S + 'v')
                    text = (value.text or '') if value is not None else ''
                    if cell.get('t') == 's':
                        position = int(text)
                        if position < 0:
                            raise ValueError()
                        text = shared[position]
                    elif cell.get('t') == 'inlineStr':
                        text = ''.join(t.text or '' for t in cell.findall('.//' + S + 't'))
                    elif text and cell.get('t') not in (None, 'str'):
                        raise ValueError()
                    if text.strip():
                        values[re.match(r'[A-Z]+', ref)[0]] = text.strip()
                if not values:
                    continue
                if set(values) != {'A', 'B', 'C'}:
                    raise ValueError()
                rows.append((number, [values[c] for c in 'ABC']))
            rows.sort()
            if not rows or rows[0][1] != HEADER:
                raise ValueError()
            rules = [dict(rowKey=f'row:{number}', rowNumber=number, phraseId=values[0],
                          phrase=values[1], materialText=values[2], materials=_materials(values[2], number))
                     for number, values in rows[1:]]
            if not rules:
                raise ValueError()
        _check(cancel_check)
        return dict(filename=FILENAME, sheetName=SHEET_NAME, raw_bytes=raw,
                    content_hash=hashlib.sha256(raw).hexdigest(), rules=rules)
    except (OSError, ValueError, TypeError, KeyError, IndexError, BadZipFile, E.ParseError):
        raise PhraseLibraryError('话术库缺失、损坏、正在更新或格式不完整') from None
