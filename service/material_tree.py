"""Source directory catalogs and deterministic product/material trees."""
import asyncio
from copy import deepcopy
import io
import re
from xml.etree import ElementTree as E
from zipfile import ZipFile

if __package__:
    from .docx_reader import W, Numbering
else:
    from docx_reader import W, Numbering


def matches_business_stage(title, business_data):
    """Compare only a first-level heading; headings without stage words stay eligible."""
    aliases = {'放贷': '放款', '放款': '放款', '签约': '签约',
               '抵押': '抵押', '质押': '质押', '保证': '保证'}

    def terms(text):
        text = re.sub(r'\s+', '', text)
        return {canonical for word, canonical in aliases.items() if word in text}

    value = (business_data or {}).get('grtstlRcmbNm') or ''
    requested, directory = terms(value), terms(title)
    return not requested or not directory or bool(requested & directory)


def rule_category_path(rule):
    return [dict(categoryId=g['sourceRuleId'], categoryName=g['title']) for g in rule.get('groups', [])]


def catalog_from_rules(rules, product_names):
    catalog = {name: [] for name in product_names}
    for rule in rules:
        path = rule_category_path(rule)
        paths = catalog.setdefault(rule['productName'], [])
        for depth in range(1, len(path) + 1):
            if path[:depth] not in paths:
                paths.append(deepcopy(path[:depth]))
    return catalog


def build_category_catalog(snapshots, cancel_check=lambda: False):
    """Read all Word headings, including the last heading with no detail rows.

    Called after the existing strict rule index validates these same snapshots.
    Catalog paths keep original labels and order; no current filesystem reads.
    """
    catalog = {}
    for snap in snapshots:
        if cancel_check():
            raise asyncio.CancelledError()
        paths = catalog.setdefault(snap['name'], [])
        with ZipFile(io.BytesIO(snap['raw_bytes'])) as archive:
            root = E.fromstring(archive.read('word/document.xml'))
            numbering = Numbering(archive)

        def paragraph(p):
            return numbering.label(p) + ''.join((n.text or '') if n.tag == W + 't' else
                '\n' if n.tag in (W + 'br', W + 'cr') else '\t' if n.tag == W + 'tab' else '' for n in p.iter())

        for table in root.iter(W + 'tbl'):
            groups = []
            for row in table.findall(W + 'tr'):
                if cancel_check():
                    raise asyncio.CancelledError()
                cells = row.findall(W + 'tc')
                values = ['\n'.join(paragraph(p) for p in c.findall(W + 'p')) for c in cells]
                if len(cells) != 2:
                    continue
                identifier, title = values[0].strip(), values[1]
                groups = [g for g in groups if identifier.startswith(g['categoryId'] + '.')]
                groups.append(dict(categoryId=identifier, categoryName=title))
                if groups not in paths:
                    paths.append(deepcopy(groups))
    return catalog


def select_category(catalog, product, identifiers):
    """An empty model selection means the last original first-level category."""
    if not isinstance(identifiers, list) or any(not isinstance(i, str) or not i.strip() for i in identifiers):
        raise ValueError('Invalid category path')
    paths = catalog.get(product, [])
    if identifiers:
        matches = [p for p in paths if [g['categoryId'] for g in p] == identifiers]
        if len(matches) != 1:
            raise ValueError('Category is not in this product Word catalog')
        return deepcopy(matches[0])
    roots = [p for p in paths if len(p) == 1]
    return deepcopy(roots[-1]) if roots else []


def iter_materials(items):
    """Leaves in display order: child directories, then direct materials."""
    def walk(categories):
        for category in categories:
            yield from walk(category['children'])
            yield from category['materials']
    for product in items:
        yield from walk(product['categories'])


def group_materials(products, located_items, catalog, cancel_check=lambda: False):
    """Group (material, source path) pairs, prune empties, and reassign item IDs."""
    result = []
    for product in products:
        if cancel_check():
            raise asyncio.CancelledError()
        name = product['productName']
        roots, by_path = [], {}

        def ensure(path):
            siblings, parent = roots, ()
            for group in path:
                identifier, title = group['categoryId'], group['categoryName']
                if not isinstance(identifier, str) or not identifier.strip() or not isinstance(title, str) or not title.strip():
                    raise ValueError('Empty source directory')
                key = (*parent, identifier)
                if key not in by_path:
                    node = dict(categoryId=identifier, categoryName=title, children=[], materials=[])
                    siblings.append(node)
                    by_path[key] = node
                node = by_path[key]
                if node['categoryName'] != title:
                    raise ValueError('Ambiguous source directory ID')
                siblings, parent = node['children'], key
            return node

        for path in catalog.get(name, []):
            ensure(path)
        for item, path in located_items:
            if cancel_check():
                raise asyncio.CancelledError()
            if item['productName'] == name:
                path = path or [dict(categoryId='UNCATEGORIZED', categoryName='未分类材料')]
                ensure(path)['materials'].append(deepcopy(item))

        def prune(categories):
            kept = []
            for node in categories:
                node['children'] = prune(node['children'])
                if node['children'] or node['materials']:
                    kept.append(node)
            return kept

        result.append(dict(productName=name, categories=prune(roots)))
    for index, item in enumerate(iter_materials(result), 1):
        if cancel_check():
            raise asyncio.CancelledError()
        item['itemId'] = f'R{index:04d}'
    return result
