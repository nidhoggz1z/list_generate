"""T15: lossless row inventory from T14 byte snapshots, without applicability decisions."""
import asyncio
import hashlib
import io
import json
import re
from copy import deepcopy
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

if __package__:
    from .docx_reader import W, Numbering, read_docx, value
else:
    from docx_reader import W, Numbering, read_docx, value


class RuleInventoryError(Exception):
    """A snapshot cannot be indexed completely."""


def build_rule_inventory(snapshots, cancel_check=lambda: False):
    """Return ordered detail rows; ruleKey is a JSON tuple of product and location.

    sourcePosition uses one-based table/row positions. cells retain paragraph and
    explicit line breaks, automatic labels and original source IDs. groups retain
    merged heading rows. Unsupported layouts fail the entire inventory.
    """
    result, names = [], set()
    if not isinstance(snapshots, (list, tuple)) or not snapshots:
        raise RuleInventoryError('清单快照为空')
    for snap in snapshots:
        if cancel_check():
            raise asyncio.CancelledError()
        try:
            name, raw = snap['name'], snap['raw_bytes']
            if not isinstance(name, str) or not name.strip() or name in names:
                raise ValueError()
            names.add(name)
            if (hashlib.sha256(raw).hexdigest() != snap['content_hash']
                    or read_docx(io.BytesIO(raw)) != snap['content']):
                raise ValueError()
            with ZipFile(io.BytesIO(raw)) as archive:
                root = ET.fromstring(archive.read('word/document.xml'))
                numbering = Numbering(archive)
            body = root.find(W + 'body')
            if body is None:
                raise ValueError()

            def paragraph(p):
                parts = []
                for child in p.iter():
                    if child.tag == W + 't':
                        parts.append(child.text or '')
                    elif child.tag == W + 'tab':
                        parts.append('\t')
                    elif child.tag in (W + 'br', W + 'cr'):
                        parts.append('\n')
                return numbering.label(p) + ''.join(parts)

            def blocks(container):
                for child in container:
                    if child.tag in (W + 'sdt', W + 'sdtContent', W + 'customXml'):
                        yield from blocks(child)
                    elif child.tag in (W + 'p', W + 'tbl'):
                        yield child
                    elif child.tag not in (W + 'sectPr', W + 'tcPr'):
                        raise ValueError()

            groups, context, table_count = [], [], 0
            start = len(result)
            for block in blocks(body):
                if cancel_check():
                    raise asyncio.CancelledError()
                if block.tag == W + 'p':
                    context.append(paragraph(block))
                    continue
                table_count += 1
                if any(child.tag not in (W + 'tblPr', W + 'tblGrid', W + 'tr') for child in block):
                    raise ValueError()
                rows = block.findall(W + 'tr')
                if not rows:
                    raise ValueError()
                for row_number, row in enumerate(rows, 1):
                    if cancel_check():
                        raise asyncio.CancelledError()
                    cells = row.findall(W + 'tc')
                    if any(child.tag not in (W + 'trPr', W + 'tblPrEx', W + 'tc') for child in row):
                        raise ValueError()
                    texts, spans = [], []
                    for cell in cells:
                        props = cell.find(W + 'tcPr')
                        # No vertical/horizontal legacy merges in the 75 audited files.
                        if props is not None and any(props.find(W + tag) is not None for tag in ('vMerge', 'hMerge')):
                            raise ValueError()
                        spans.append(int(value(props, 'gridSpan', '1')))
                        content = list(blocks(cell))
                        if any(p.tag != W + 'p' for p in content):
                            raise ValueError()
                        texts.append('\n'.join(paragraph(p) for p in content))
                    if row_number == 1:
                        if (len(texts) != 5 or texts[0] != '编号'
                                or texts[1] not in ('材料名称', '材料名称或核查事项')
                                or texts[2:] != ['提供要求', '适用条件及材料要求', '规程依据'] or spans != [1] * 5):
                            raise ValueError()
                        groups = []
                        continue
                    position = f'table:{table_count}/row:{row_number}'
                    if len(texts) == 2 and spans == [1, 4] and all(t.strip() for t in texts):
                        identifier = texts[0].strip()
                        if not re.fullmatch(r'\d+(\.\d+)*', identifier):
                            raise ValueError()
                        groups = [g for g in groups if identifier.startswith(g['sourceRuleId'] + '.')]
                        groups.append(dict(sourceRuleId=identifier, title=texts[1], sourcePosition=position))
                    elif len(texts) == 5 and spans == [1] * 5 and texts[1].strip():
                        result.append(dict(productName=name,
                            ruleKey=json.dumps([name, position], ensure_ascii=False, separators=(',', ':')),
                            sourceRuleId=texts[0].strip() or position, sourcePosition=position,
                            cells=texts, groups=deepcopy(groups), rawMarker=texts[2],
                            kind='CHECK' if '核查项' in texts[2] else 'MATERIAL'))
                    else:
                        raise ValueError()
            if len(result) == start:
                raise ValueError()
            for rule in result[start:]:
                rule['documentContext'] = '\n'.join(context)
        except (KeyError, TypeError, ValueError, AttributeError, BadZipFile, ET.ParseError, OSError):
            raise RuleInventoryError('清单快照无效或表格无法完整建立索引') from None
    return result
