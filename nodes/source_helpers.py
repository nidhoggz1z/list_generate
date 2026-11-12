"""Source-derived pure helpers; old project remains read-only."""

import asyncio
import json

import re

from copy import deepcopy

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
    """Group (material, source path) pairs and prune empty source directories."""
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
    return result

def _split_display_metadata(text, default_field=None):
    """Split labeled regulation cells; publication references are not display fields."""
    fields = {'来源系统': 'sourceSystem', '制度依据': 'policyBasis',
              '发布部门': 'publishDepartment', '发布日期': 'publishDate'}
    parts = re.split(r'[ \t]*(来源系统|制度依据|发布依据|发布部门|发布日期)[ \t]*[：:][ \t]*', text)
    if len(parts) == 1:
        return None
    values = {fields[label]: value.strip() for label, value in zip(parts[1::2], parts[2::2])
              if label in fields and value.strip()}
    if default_field and parts[0].strip():
        values.setdefault(default_field, parts[0].strip())
    return values

def display_metadata(instance, regulation=''):
    """Normalize inline/multiline labels without changing source data."""
    defaults = dict(sourceSystem='上银办', policyBasis='', publishDate='2026年', publishDepartment='上海银行')
    metadata, unpacked = dict(instance), {}
    for field in defaults:
        packed = _split_display_metadata(instance.get(field, ''), field)
        if packed is not None:
            metadata[field] = ''
            unpacked.update(packed)
    metadata.update(unpacked)
    metadata.update(_split_display_metadata(regulation) or {})
    return {field: metadata.get(field, '') if metadata.get(field, '').strip() else default
            for field, default in defaults.items()}

def material_name(product, instance, *, legacy=False):
    name = instance['materialName']
    prefix = f'[{product}]'
    if name.startswith(prefix):
        name = name[len(prefix):]
    subject, requirement = instance['subject'], instance['requirement']
    if not name.strip():
        return ''
    if legacy:
        # Historical category matching must use the title stored by the old formatter.
        if subject.strip() and subject not in name:
            name = subject + name
        if requirement.strip() and requirement not in name:
            name += f'（{requirement}）'
        return prefix + name
    if requirement.strip():
        name = name.removesuffix(f'（{requirement}）')
    if subject.strip():
        if name.endswith(f'（{subject}）'):
            name = name.removesuffix(f'（{subject}）')
        elif name.startswith(subject):
            name = name[len(subject):]
        name += f'（{subject}）'
    if requirement.strip() and requirement not in name:
        name += f'（{requirement}）'
    return prefix + name

class MaterialFactError(Exception):
    """Invalid extraction or business context."""

ALIASES = {'担保': '担保方式', '额度': '金额', '借款人名称': '客户名称',
           'cstNm': '客户名称', 'gnrNm': '担保人姓名', 'subcontractNum': '从合同编号',
           'cltlTp': '押品类型', 'cltlNm': '押品名称', 'ctrTamt': '金额',
           'ccy': '币种', 'contractNum': '主合同编号', 'grtstlRcmbNm': '担保方式',
           'primGrtstlNm': '担保方式'}

IDENTITIES = {'客户名称': 'customer', '担保人姓名': 'guarantor',
              '共同担保人姓名': 'jointGuarantor', '从合同编号': 'subcontract',
              '押品存在': 'collateral', '押品名称': 'collateral', '押品类型': 'collateral'}

NESTED = {'subcontracts', 'jntGnrInfs', 'SubcontractCollateralRels'}

ROLES = {'guarantor': '担保人', 'jointGuarantor': '共同担保人',
         'subcontract': '从合同', 'collateral': '押品'}

def _resolve_material_facts(approval, business_data, cancel_check=lambda: False):
    """Return products, effective facts and ordered objects with parentKey links.

    JSON paths identify objects even without names/IDs. Unscoped JSON objects
    apply to every PDF product (user decision 2026-10-23). PDF product scopes
    stay separate; empty PDF product applies globally. Within a scope, later
    present facts win in page/record order; absent facts never erase a value.
    Raw code/extension fields remain opaque. Inputs are never mutated.
    """
    def check():
        if cancel_check():
            raise asyncio.CancelledError()

    check()
    try:
        business = deepcopy(business_data)
        products = deepcopy(approval['products'])
        names = [p['productName'] for p in products]
        if not names or any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names):
            raise ValueError()
        pdf = deepcopy(approval['facts'])
        for f in pdf:
            if (f['productName'] and f['productName'] not in names
                    or type(f['present']) is not bool or type(f['negated']) is not bool
                    or not isinstance(f['subject'], str) or not isinstance(f['name'], str)
                    or not f['name'].strip() or not isinstance(f['value'], str)
                    or type(f['page_number']) is not int or f['page_number'] < 1
                    or not isinstance(f['source'], str)
                    or f['present'] and (not f['value'].strip() or not f['source'].strip())
                    or not f['present'] and (f['negated'] or f['value'] or f['source'])):
                raise ValueError()
            f['name'] = ALIASES.get(f['name'], f['name'])
        pdf.sort(key=lambda f: f['page_number'])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise MaterialFactError('批复事实或业务上下文无效') from None
    all_facts, all_objects = [], []
    for product in names:
        check()
        objects, resolved, scopes = [], {}, {}

        def new_object(kind, path, name='', parent='', aliases=()):
            obj = dict(objectKey=f'{names.index(product)}:{path}', productName=product,
                       kind=kind, name=name, parentKey=parent, source=path, present=True,
                       aliases=[a for a in aliases if a])
            if kind == 'collateral':
                obj['typeState'] = 'unknown'
            objects.append(obj)
            return obj

        def put(name, value, obj, source, origin='JSON', present=True, negated=False, page=None):
            name = ALIASES.get(name, name)
            key = (obj['objectKey'], name)
            if not present and key in resolved and resolved[key]['present']:
                return
            fact = dict(name=name, value=deepcopy(value), present=present, negated=negated,
                        state='unknown' if not present else 'explicit-negative' if negated else 'present',
                        origin=origin, source=source, page_number=page, productName=product,
                        subject=obj['name'], objectKey=obj['objectKey'])
            resolved[key] = fact
            if name in ('客户名称', '担保人姓名', '共同担保人姓名', '押品名称', '从合同编号') and present and not negated:
                obj['name'] = value
            if name == '押品类型' and obj['kind'] == 'collateral':
                obj['typeState'] = fact['state']

        def json_fields(data, obj, path, renames=None):
            for key, value in data.items():
                check()
                if (key in NESTED or value is None or isinstance(value, str) and not value.strip()
                        or value == [] or value == {}):
                    continue
                put((renames or {}).get(key, key), value, obj, f'{path}.{key}')

        customer = new_object('customer', '$', business.get('cstNm', ''), aliases=[business.get('cstId', ''), '借款人', '客户', ''])
        json_fields(business, customer, '$')
        for i, sub in enumerate(business.get('subcontracts', [])):
            check()
            path = f'$.subcontracts[{i}]'
            parent = new_object('subcontract', path, sub.get('subcontractNum', ''), customer['objectKey'])
            json_fields({k: v for k, v in sub.items() if k not in ('gnrNm', 'gnrId')}, parent, path)
            if sub.get('gnrNm', '').strip() or sub.get('gnrId', '').strip():
                obj = new_object('guarantor', path + '.guarantor', sub.get('gnrNm', ''), parent['objectKey'], [sub.get('gnrId', '')])
                json_fields({k: sub[k] for k in ('gnrNm', 'gnrId') if k in sub}, obj, path)
            for j, joint in enumerate(sub.get('jntGnrInfs', [])):
                if not any(isinstance(v, str) and v.strip() for v in joint.values()):
                    continue
                joint_path = f'{path}.jntGnrInfs[{j}]'
                obj = new_object('jointGuarantor', joint_path, joint.get('cstNm', ''), parent['objectKey'], [joint.get('cstId', '')])
                json_fields(joint, obj, joint_path, {'cstNm': '共同担保人姓名'})
            for j, collateral in enumerate(sub.get('SubcontractCollateralRels', [])):
                if not any(collateral.get(k, '').strip() for k in ('cltlId', 'cltlTp', 'cltlNm')):
                    continue
                cl_path = f'{path}.SubcontractCollateralRels[{j}]'
                obj = new_object('collateral', cl_path, collateral.get('cltlNm', ''), parent['objectKey'], [collateral.get('cltlId', '')])
                json_fields(collateral, obj, cl_path)
                if not collateral.get('cltlTp', '').strip():
                    put('押品类型', '', obj, cl_path + '.cltlTp', present=False)

        # Exact source identities only; no fuzzy person/product matching.
        for index, fact in enumerate(pdf):
            check()
            if fact['productName'] and fact['productName'] != product:
                continue
            subject, name = fact['subject'], fact['name']
            kind = IDENTITIES.get(name)
            candidates = [o for o in objects if subject and (subject == o['name'] or subject in o['aliases'])
                          and (kind is None or o['kind'] == kind)]
            if kind and not candidates and name != '客户名称':
                candidates = [o for o in objects if o['kind'] == kind and fact['value'] and
                              fact['value'] in [o['name'], *o['aliases']]]
            general = subject in ('', '借款人', '客户', customer['name'], *customer['aliases'])
            if kind and subject == ROLES.get(kind):
                candidates = [o for o in objects if o['kind'] == kind]
            if (subject, kind) in scopes:
                candidates = [scopes[(subject, kind)]]
            if name == '客户名称' or general and (fact['negated'] or name == '押品存在'):
                obj = customer
            elif len(candidates) == 1:
                obj = candidates[0]
            elif len(candidates) > 1:
                obj = new_object('subject', f'pdf:{index}', subject, aliases=[subject])
                obj['relatedObjectKeys'] = [o['objectKey'] for o in candidates]
            elif not fact['present']:
                obj = customer if general else new_object('subject', f'pdf:{index}', subject)
            elif kind:
                obj = new_object(kind, f'pdf:{index}', subject if subject not in ('借款人', '客户') else '',
                                 aliases=[subject])
            elif general:
                obj = customer
            else:
                obj = new_object('subject', f'pdf:{index}', subject, aliases=[subject])
            if obj['kind'] == 'subject' and subject:
                scopes[(subject, kind)] = obj
            put(name, fact['value'], obj, fact['source'], 'PDF', fact['present'], fact['negated'], fact['page_number'])

        presence = resolved.get((customer['objectKey'], '押品存在'))
        if (presence and presence['present'] and not presence['negated']
                and not any(o['kind'] == 'collateral' for o in objects)):
            new_object('collateral', 'pdf:collateral', parent=customer['objectKey'])['source'] = presence['source']

        # Explicit absence blocks JSON supplementation within precisely this scope.
        removed = set()
        for fact in resolved.values():
            if fact['origin'] != 'PDF' or not fact['negated']:
                continue
            obj = next(o for o in objects if o['objectKey'] == fact['objectKey'])
            if fact['name'] == '担保方式' and obj is customer:
                removed.update(o['objectKey'] for o in objects if o['kind'] in ('subcontract', 'guarantor', 'jointGuarantor', 'collateral'))
            elif fact['name'] == '担保方式' and obj['kind'] in ('guarantor', 'jointGuarantor'):
                removed.add(obj['objectKey'])
            elif obj is customer and fact['name'] in ('押品存在', '担保人姓名', '共同担保人姓名'):
                role = IDENTITIES[fact['name']]
                removed.update(o['objectKey'] for o in objects if o['kind'] == role)
            elif fact['name'] in ('押品存在', '担保人姓名', '共同担保人姓名'):
                removed.add(obj['objectKey'])
        for obj in objects:
            if obj['objectKey'] in removed and any(f['objectKey'] == obj['objectKey'] and f['origin'] == 'PDF' and f['negated'] for f in resolved.values()):
                # Keep a source scope for explicit denial, without an applicable object.
                obj.update(kind='subject', present=False)
                obj.pop('typeState', None)
            if obj['objectKey'] not in removed or not obj['present']:
                if obj['parentKey'] in removed:
                    obj['parentKey'] = ''
                if 'relatedObjectKeys' in obj:
                    obj['relatedObjectKeys'] = [k for k in obj['relatedObjectKeys'] if k not in removed]
                obj.pop('aliases')
                all_objects.append(obj)
        all_facts.extend(f for f in resolved.values() if f['objectKey'] not in removed or f['origin'] == 'PDF' and f['negated'])
    check()
    return dict(products=products, facts=all_facts, objects=all_objects)


class MaterialGenerationError(Exception):
    """Safe failure without model output, business content or credentials."""

_KINDS = {'customer', 'subcontract', 'guarantor', 'jointGuarantor', 'collateral', 'subject'}

def _check(cancel_check):
    if cancel_check():
        raise asyncio.CancelledError()

def _text(value, required=False):
    if not isinstance(value, str) or required and not value.strip():
        raise ValueError()
    return value

def _directory_exclusion(rule, business_data):
    groups = rule.get('groups', [])
    return bool(groups and not matches_business_stage(groups[0]['title'], business_data))

def _excluded_decision(rule, reason):
    return dict(productName=rule['productName'], ruleKey=rule['ruleKey'],
        action='CHECK' if rule['kind'] == 'CHECK' else 'EXCLUDE', applicability='NOT_APPLICABLE',
        objectKinds=[], scopeObjectKey='', reason=reason,
        evidence=[dict(type='RULE', ruleKey=rule['ruleKey'], quote=rule['cells'][1])],
        instances=[], excludedObjects=[])

def validate_material_decisions(decisions, rules, resolved_context, cancel_check=lambda: False,
                                business_data=None):
    """Check exact rule coverage, references and declared-scope object coverage.

    Return a copy in original rule/object order. Only explicit applicability may
    retain materials. Unknown conditions or incomplete object coverage exclude;
    excluded objects are never restored because another object is applicable.
    Source existence/scope is verifiable; meaning of scope/quotes remains T28.
    """
    _check(cancel_check)
    try:
        expected = {r['ruleKey']: r for r in rules}
        objects = {o['objectKey']: o for o in resolved_context['objects']}
        facts = resolved_context['facts']
        if not expected or len(expected) != len(rules) or not isinstance(decisions, list):
            raise ValueError()
        if len(objects) != len(resolved_context['objects']):
            raise ValueError()
        by_key = {}
        for d in deepcopy(decisions):
            _check(cancel_check)
            key = d['ruleKey']
            if key not in expected or key in by_key:
                raise ValueError()
            if _directory_exclusion(expected[key], business_data):
                d = _excluded_decision(expected[key], '一级目录与 grtstlRcmbNm 不匹配，整目录排除。')
            d.setdefault('applicability', 'UNKNOWN')
            if set(d) != {'productName', 'ruleKey', 'action', 'applicability', 'objectKinds', 'scopeObjectKey',
                          'reason', 'evidence', 'instances', 'excludedObjects'}:
                raise ValueError()
            rule = expected[key]
            product = rule['productName']
            if d['productName'] != product or d['action'] not in ('KEEP', 'EXCLUDE', 'CHECK'):
                raise ValueError()
            if (rule['kind'] == 'CHECK') != (d['action'] == 'CHECK'):
                raise ValueError()
            if d['applicability'] not in ('APPLICABLE', 'NOT_APPLICABLE', 'UNKNOWN'):
                raise ValueError()
            _text(d['reason'], True)
            kinds, scope = d['objectKinds'], _text(d['scopeObjectKey'])
            if not isinstance(kinds, list) or len(set(kinds)) != len(kinds) or not set(kinds) <= _KINDS:
                raise ValueError()

            def lineage(obj_key):
                chain = []
                while obj_key:
                    if obj_key in chain or obj_key not in objects or objects[obj_key]['productName'] != product:
                        raise ValueError()
                    chain.append(obj_key)
                    obj_key = objects[obj_key]['parentKey']
                return chain

            if scope and (not objects[scope]['present'] or not lineage(scope)):
                raise ValueError()
            targets = [o['objectKey'] for o in objects.values() if o['productName'] == product
                       and o['present'] and o['kind'] in kinds
                       and (not scope or scope in lineage(o['objectKey']))]
            targets = targets or ['']

            uncertain = False

            def evidence(refs, target=None, exclusion=False):
                nonlocal uncertain
                if not isinstance(refs, list):
                    raise ValueError()
                if not refs:
                    if exclusion:
                        return False
                    raise ValueError()
                has_rule, has_object, explicit = False, False, False
                citations_valid = True
                allowed = lineage(target) if target else None
                for ref in refs:
                    _check(cancel_check)
                    if ref['type'] == 'RULE':
                        if set(ref) != {'type', 'ruleKey', 'quote'} or ref['ruleKey'] != key:
                            raise ValueError()
                        quote = _text(ref['quote'])
                        if not quote.strip() or not any(quote in c for c in rule['cells']):
                            if not exclusion:
                                raise ValueError()
                            citations_valid = False
                        has_rule = True
                        continue
                    obj_key = ref['objectKey']
                    obj = objects[obj_key]
                    if obj['productName'] != product or allowed is not None and obj_key not in allowed:
                        raise ValueError()
                    if ref['type'] == 'OBJECT':
                        if set(ref) != {'type', 'objectKey', 'source'}:
                            raise ValueError()
                        if _text(ref['source']) != obj['source']:
                            if not exclusion:
                                raise ValueError()
                            citations_valid = False
                        has_object |= obj_key == target and obj['present']
                    elif ref['type'] == 'FACT':
                        if set(ref) != {'type', 'objectKey', 'name', 'source'}:
                            raise ValueError()
                        _text(ref['name'], True)
                        _text(ref['source'])
                        matches = [f for f in facts if f['objectKey'] == obj_key and f['productName'] == product
                                   and f['name'] == ref['name'] and f['source'] == ref['source']]
                        if not matches:
                            if not exclusion:
                                raise ValueError()
                            citations_valid = False
                        confirmed = any(f['present'] and f['state'] != 'unknown' and ref['source'] for f in matches)
                        if not confirmed and not exclusion:
                            if not matches:
                                raise ValueError()
                            uncertain = True
                        has_object |= obj_key == target and bool(matches)
                        explicit |= bool(confirmed)
                    else:
                        raise ValueError()
                if not exclusion and (not has_rule or target and not has_object):
                    raise ValueError()
                return not exclusion or bool(explicit and has_rule and citations_valid)

            supported = evidence(d['evidence'], exclusion=d['action'] == 'EXCLUDE')
            if not isinstance(d['instances'], list) or not isinstance(d['excludedObjects'], list):
                raise ValueError()
            exclude_whole = (d['action'] == 'KEEP' and
                             (d['applicability'] != 'APPLICABLE' or bool(kinds) and targets == ['']))
            if d['action'] != 'KEEP':
                if d['instances'] or d['excludedObjects']:
                    raise ValueError()
                if d['action'] == 'EXCLUDE':
                    for target in targets:
                        refs = [r for r in d['evidence'] if r['type'] == 'RULE'
                                or not target or r['objectKey'] in lineage(target)]
                        supported &= evidence(refs, target, True)
                    if not supported and not _directory_exclusion(rule, business_data):
                        d['evidence'] = [dict(type='RULE', ruleKey=key, quote=rule['cells'][1])]
                        d['applicability'] = 'UNKNOWN'
                        d['reason'] = '适用性不确定，按严格审核排除。'
            else:
                seen = set()
                for field in ('instances', 'excludedObjects'):
                    for item in d[field]:
                        _check(cancel_check)
                        target = item['objectKey']
                        # Validate sources before excluding unbound/incomplete output.
                        unbound = field == 'instances' and target == '' and targets != ['']
                        if target in seen or (target not in targets and not unbound):
                            raise ValueError()
                        seen.add(target)
                        _text(item['reason'], True)
                        if field == 'instances':
                            required = {'objectKey', 'subject', 'materialName', 'requirement', 'reason', 'evidence'}
                            optional = {'sourceSystem', 'policyBasis', 'publishDate', 'publishDepartment'}
                            if not required <= set(item) or set(item) - required - optional:
                                raise ValueError()
                            for name in required - {'evidence'} | (set(item) & optional):
                                _text(item[name], name == 'materialName')
                            if item['subject'] != (objects[target]['name'] if target else ''):
                                raise ValueError()
                        elif set(item) != {'objectKey', 'reason', 'evidence'}:
                            raise ValueError()
                        supported = evidence(item['evidence'], target, field == 'excludedObjects')
                        exclude_whole |= unbound
                        if field == 'excludedObjects' and not supported:
                            item['evidence'] = [dict(type='RULE', ruleKey=key, quote=rule['cells'][1])]
                            if target:
                                item['evidence'].append(dict(type='OBJECT', objectKey=target,
                                                             source=objects[target]['source']))
                            item['reason'] = '适用性不确定，按严格审核排除。'
                if seen != set(targets):
                    exclude_whole = True
                if not exclude_whole:
                    d['instances'].sort(key=lambda i: targets.index(i['objectKey']))
                    d['excludedObjects'].sort(key=lambda i: targets.index(i['objectKey']))
            if d['action'] == 'KEEP' and (exclude_whole or uncertain or not d['instances']):
                d.update(action='EXCLUDE', applicability='UNKNOWN',
                    reason='适用性不确定，按严格审核排除。',
                    evidence=[dict(type='RULE', ruleKey=key, quote=rule['cells'][1])],
                    instances=[], excludedObjects=[])
            by_key[key] = d
        if set(by_key) != set(expected):
            raise ValueError()
        return [by_key[r['ruleKey']] for r in rules]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise MaterialGenerationError('材料决定无效：规则覆盖、处理类型、来源或对象展开校验失败') from None


def catalog_from_rules(rules, product_names):
    catalog = {name: [] for name in product_names}
    for rule in rules:
        path = rule_category_path(rule)
        paths = catalog.setdefault(rule['productName'], [])
        for depth in range(1, len(path) + 1):
            if path[:depth] not in paths:
                paths.append(deepcopy(path[:depth]))
    return catalog

class PhraseMaterialError(Exception):
    """Safe all-or-nothing failure of supplemental generation."""

def validate_phrase_materials(decisions, phrase_rules, approval, word_rules, resolved_context,
                              cancel_check=lambda: False, category_catalog=None):
    """Require exact row/product/leaf decisions and source/object references.

    Semantic equivalence and the declared product/subject scope remain model
    judgements. A quote must be a real nonempty continuous span of one page.
    """
    _check(cancel_check)
    try:
        expected = {r['rowKey']: r for r in phrase_rules}
        products = [p['productName'] for p in approval['products']]
        catalog = category_catalog if category_catalog is not None else catalog_from_rules(word_rules, products)
        pages = {p['page_number']: p['text'] + '\n' + '\n'.join('\t'.join(row) for row in p['tables'])
                 for p in approval['pages']}
        words = {r['ruleKey']: r for r in word_rules}
        objects = {o['objectKey']: o for o in resolved_context['objects']}
        if not isinstance(decisions, list) or not expected or len(expected) != len(phrase_rules):
            raise ValueError()
        by_key = {}
        for d in deepcopy(decisions):
            _check(cancel_check)
            if set(d) != {'rowKey', 'scope', 'matches', 'products'}:
                raise ValueError()
            key = d['rowKey']
            if key not in expected or key in by_key or d['scope'] not in ('NONE', 'EXPLICIT', 'COMMON', 'UNKNOWN'):
                raise ValueError()
            if not isinstance(d['matches'], list) or not isinstance(d['products'], list):
                raise ValueError()
            if not d['matches']:
                if d['scope'] != 'NONE' or d['products']:
                    raise ValueError()
            else:
                if d['scope'] == 'NONE' or not d['products']:
                    raise ValueError()
                for match in d['matches']:
                    if (set(match) != {'page_number', 'quote'} or type(match['page_number']) is not int
                            or match['page_number'] not in pages
                            or _text(match['quote'], True) not in pages[match['page_number']]):
                        raise ValueError()
                names = []
                leaves = {m['materialKey']: m for m in expected[key]['materials']}
                for product in d['products']:
                    _check(cancel_check)
                    if set(product) != {'productName', 'materials'}:
                        raise ValueError()
                    name = product['productName']
                    if name not in products or name in names or not isinstance(product['materials'], list):
                        raise ValueError()
                    names.append(name)
                    seen = set()
                    for material in product['materials']:
                        material.setdefault('applicability', 'UNKNOWN')
                        if set(material) != {'materialKey', 'applicability', 'categoryPath', 'instances'}:
                            raise ValueError()
                        state = material['applicability']
                        if state not in ('APPLICABLE', 'NOT_APPLICABLE', 'UNKNOWN'):
                            raise ValueError()
                        leaf = material['materialKey']
                        if (leaf not in leaves or leaf in seen or not isinstance(material['instances'], list)
                                or state == 'APPLICABLE' and not material['instances']):
                            raise ValueError()
                        seen.add(leaf)
                        identifiers = material['categoryPath']
                        if not isinstance(identifiers, list):
                            raise ValueError()
                        path = select_category(catalog, name, identifiers) if identifiers else []
                        material['categoryPath'] = [g['categoryId'] for g in path]
                        identities = set()
                        unlocated = False
                        for instance in material['instances']:
                            _check(cancel_check)
                            if set(instance) != {'objectKey', 'subject', 'materialName', 'requirement', 'wordRuleKey'}:
                                raise ValueError()
                            for field in instance:
                                _text(instance[field], field == 'materialName')
                            obj, word = instance['objectKey'], instance['wordRuleKey']
                            if obj:
                                if (obj not in objects or not objects[obj]['present']
                                        or objects[obj]['productName'] != name or instance['subject'] != objects[obj]['name']):
                                    raise ValueError()
                            elif instance['subject']:
                                raise ValueError()
                            if word and (word not in words or words[word]['productName'] != name):
                                raise ValueError()
                            exact = [r for r in word_rules if r['productName'] == name
                                     and r['cells'][1] == instance['materialName']]
                            unlocated |= not path and not word and len(exact) != 1
                            identity = (obj, word)
                            if identity in identities:
                                raise ValueError()
                            identities.add(identity)
                            # Names come from an allowed source; contracts may adapt to the product.
                            if not word and instance['materialName'] != leaves[leaf]['name'] and '合同' not in leaves[leaf]['name']:
                                raise ValueError()
                        if unlocated:
                            material['applicability'] = 'UNKNOWN'
                        if state != 'APPLICABLE' or unlocated:
                            material['instances'] = []
                    if seen != set(leaves):
                        raise ValueError()
                    product['materials'].sort(key=lambda m: list(leaves).index(m['materialKey']))
                d['products'].sort(key=lambda p: products.index(p['productName']))
                if d['scope'] == 'UNKNOWN':
                    d.update(scope='NONE', matches=[], products=[])
            by_key[key] = d
        if set(by_key) != set(expected):
            raise ValueError()
        return [by_key[r['rowKey']] for r in phrase_rules]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PhraseMaterialError('话术补充无效：话术覆盖、批复引用、整包材料、产品或主体校验失败') from None

def merge_phrase_materials(word_rules, word_decisions, phrase_decisions, phrase_snapshot,
                           cancel_check=lambda: False, category_catalog=None, business_data=None):
    """Merge validated bundles before public projection; no mutation of inputs.

    Word identity/name wins; confirmed Excel requirements win after stage gates.
    Only merge equal product/object identities; unknown applicability is skipped.
    """
    rules, decisions = deepcopy(word_rules), deepcopy(word_decisions)
    by_rule = {r['ruleKey']: r for r in rules}
    by_decision = {d['ruleKey']: d for d in decisions}
    rows = {r['rowKey']: r for r in phrase_snapshot['rules']}
    catalog = category_catalog if category_catalog is not None else catalog_from_rules(
        word_rules, list(dict.fromkeys(r['productName'] for r in word_rules)))
    added = {}
    exact_words = {}
    for rule in rules:
        exact_words.setdefault((rule['productName'], rule['cells'][1]), []).append(rule['ruleKey'])

    def combine(old, new):
        return old if not new or new in old else new if not old else old + '；' + new

    for d in phrase_decisions:
        _check(cancel_check)
        if d['scope'] in ('NONE', 'UNKNOWN'):
            continue
        row = rows[d['rowKey']]
        leaves = {m['materialKey']: m for m in row['materials']}
        reason = f'话术库第{row["rowNumber"]}行（{row["phraseId"]}）命中：' + '；'.join(
            f'批复第{m["page_number"]}页“{m["quote"]}”' for m in d['matches'])
        for product in d['products']:
            for material in product['materials']:
                if material.get('applicability') != 'APPLICABLE':
                    continue
                leaf = leaves[material['materialKey']]
                for instance in material['instances']:
                    _check(cancel_check)
                    name, obj = product['productName'], instance['objectKey']
                    key = instance['wordRuleKey']
                    if not key:
                        candidates = exact_words.get((name, instance['materialName']), [])
                        if len(candidates) == 1:
                            key = candidates[0]
                        elif len(candidates) > 1:
                            continue
                    if not key and not material['categoryPath']:
                        continue
                    path = ([dict(categoryId=g['sourceRuleId'], categoryName=g['title'])
                             for g in by_rule[key].get('groups', [])] if key else
                            select_category(catalog, name, material['categoryPath']))
                    if path and not matches_business_stage(path[0]['categoryName'], business_data):
                        continue
                    word_match = bool(key)
                    if not key:
                        identity = (name, instance['materialName'])
                        key = added.get(identity)
                        if key is None:
                            key = json.dumps(['话术库', name, material['materialKey'], instance['materialName']], ensure_ascii=False)
                            added[identity] = key
                            rule = dict(productName=name, ruleKey=key, sourceRuleId=leaf['sourceRuleId'],
                                kind='MATERIAL', cells=[leaf['sourceRuleId'], instance['materialName'], '', leaf['context'], ''],
                                groups=[dict(sourceRuleId=g['categoryId'], title=g['categoryName']) for g in path])
                            rules.append(rule)
                            by_rule[key] = rule
                            target = dict(productName=name, ruleKey=key, action='KEEP', instances=[],
                                applicability='APPLICABLE',
                                reason=reason, evidence=[], objectKinds=[], scopeObjectKey='', excludedObjects=[])
                            decisions.append(target)
                            by_decision[key] = target
                    rule, target = by_rule[key], by_decision[key]
                    rule['kind'] = 'MATERIAL'
                    target['action'] = 'KEEP'
                    target['applicability'] = 'APPLICABLE'
                    target['reason'] = combine(target['reason'], reason)
                    target['excludedObjects'] = [o for o in target['excludedObjects'] if o['objectKey'] != obj]
                    requirement = combine(leaf['name'], instance['requirement'])
                    existing = next((i for i in target['instances'] if i['objectKey'] == obj), None)
                    if existing is None and obj:
                        # A previously generic instance becomes specific, without a second copy.
                        existing = next((i for i in target['instances'] if not i['objectKey']), None)
                        if existing is not None:
                            existing.update(objectKey=obj, subject=instance['subject'])
                    if existing is None:
                        existing = dict(objectKey=obj, subject=instance['subject'], materialName=rule['cells'][1],
                            requirement='', reason='', evidence=[])
                        if word_match:
                            for field in ('sourceSystem', 'policyBasis', 'publishDate', 'publishDepartment'):
                                value = next((i[field] for i in target['instances'] if i.get(field, '').strip()), '')
                                if value:
                                    existing[field] = value
                            existing.setdefault('policyBasis', rule['cells'][4])
                        target['instances'].append(existing)
                    # Discard the old conditional wording on the first Excel override.
                    if existing.get('_phrase_override'):
                        existing['requirement'] = combine(existing['requirement'], requirement)
                    else:
                        existing['requirement'] = requirement
                    existing['_phrase_override'] = True
                    existing['materialName'] = rule['cells'][1]
                    existing['reason'] = combine(existing['reason'], reason)
    for d in decisions:
        for instance in d['instances']:
            instance.pop('_phrase_override', None)
    _check(cancel_check)
    return rules, decisions



