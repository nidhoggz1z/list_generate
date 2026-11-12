"""Read Word body text, tables and automatic list labels without Office."""
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def value(node, child, default=None):
    element = node.find(W + child) if node is not None else None
    return element.get(W + 'val', default) if element is not None else default


def format_number(number, kind):
    if kind == 'chineseCounting':
        digits = '零一二三四五六七八九'
        if 0 <= number < 10:
            return digits[number]
        if 10 <= number < 100:
            tens, units = divmod(number, 10)
            return (digits[tens] if tens > 1 else '') + '十' + (digits[units] if units else '')
    if kind in ('lowerLetter', 'upperLetter'):
        result = ''
        while number:
            number, remainder = divmod(number - 1, 26)
            result = chr(97 + remainder) + result
        return result.upper() if kind == 'upperLetter' else result
    if kind in ('lowerRoman', 'upperRoman'):
        result = ''
        for amount, label in [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]:
            count, number = divmod(number, amount)
            result += label * count
        return result.lower() if kind == 'lowerRoman' else result
    return str(number)


class Numbering:
    def __init__(self, archive):
        self.definitions = {}
        self.counters = {}
        self.styles = {}
        if 'word/styles.xml' in archive.namelist():
            styles = ET.fromstring(archive.read('word/styles.xml'))
            self.styles = {s.get(W + 'styleId'): s for s in styles.findall(W + 'style')}
        if 'word/numbering.xml' not in archive.namelist():
            return
        root = ET.fromstring(archive.read('word/numbering.xml'))
        abstracts = {a.get(W + 'abstractNumId'): a for a in root.findall(W + 'abstractNum')}
        for num in root.findall(W + 'num'):
            abstract = abstracts.get(value(num, 'abstractNumId'))
            if abstract is None:
                continue
            levels = {int(l.get(W + 'ilvl')): l for l in abstract.findall(W + 'lvl')}
            starts = {}
            for override in num.findall(W + 'lvlOverride'):
                level = int(override.get(W + 'ilvl'))
                if override.find(W + 'lvl') is not None:
                    levels[level] = override.find(W + 'lvl')
                start = value(override, 'startOverride')
                if start is not None:
                    starts[level] = int(start)
            self.definitions[num.get(W + 'numId')] = (levels, starts)

    def label(self, paragraph):
        properties = paragraph.find(W + 'pPr')
        num_properties = properties.find(W + 'numPr') if properties is not None else None
        style_id = value(properties, 'pStyle')
        visited = set()
        while num_properties is None and style_id in self.styles and style_id not in visited:
            visited.add(style_id)
            style = self.styles[style_id]
            num_properties = style.find(W + 'pPr/' + W + 'numPr')
            style_id = value(style, 'basedOn')
        num_id = value(num_properties, 'numId')
        if num_id not in self.definitions or num_id == '0':
            return ''
        level = int(value(num_properties, 'ilvl', '0'))
        levels, starts = self.definitions[num_id]
        definition = levels.get(level)
        if definition is None:
            return ''
        counters = self.counters.setdefault(num_id, {})
        start = starts.get(level, int(value(definition, 'start', '1')))
        counters[level] = counters.get(level, start - 1) + 1
        for deeper in list(counters):
            if deeper > level:
                del counters[deeper]
        kind = value(definition, 'numFmt', 'decimal')
        if kind == 'bullet':
            return '• '
        if kind == 'none':
            return ''
        pattern = value(definition, 'lvlText', '%' + str(level + 1) + '.')

        def substitute(match):
            referenced = int(match.group(1)) - 1
            ref_definition = levels.get(referenced)
            ref_start = starts.get(referenced, int(value(ref_definition, 'start', '1')))
            return format_number(counters.get(referenced, ref_start), value(ref_definition, 'numFmt', 'decimal'))

        return re.sub(r'%([1-9])', substitute, pattern) + ' '


def read_docx(path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        numbering = Numbering(archive)

        def render(container):
            blocks = []
            for element in container:
                if element.tag == W + 'p':
                    parts = []
                    for child in element.iter():
                        if child.tag == W + 't':
                            parts.append(child.text or '')
                        elif child.tag == W + 'tab':
                            parts.append('\t')
                        elif child.tag in (W + 'br', W + 'cr'):
                            parts.append('\n')
                    text = ''.join(parts)
                    label = numbering.label(element)
                    if text or label:
                        blocks.append(label + text)
                elif element.tag == W + 'tbl':
                    rows = []
                    for row in element.findall(W + 'tr'):
                        cells = [render(cell) for cell in row.findall(W + 'tc')]
                        rows.append('\t'.join(cells))
                    blocks.append('[表格开始]\n' + '\n'.join(rows) + '\n[表格结束]')
                elif element.tag in (W + 'sdt', W + 'sdtContent', W + 'customXml'):
                    blocks.append(render(element))
            return '\n'.join(blocks)

        body = root.find(W + 'body')
        if body is None:
            raise ValueError('DOCX 缺少正文')
        content = render(body)
        if not content.strip():
            raise ValueError('DOCX 正文为空')
        return content
