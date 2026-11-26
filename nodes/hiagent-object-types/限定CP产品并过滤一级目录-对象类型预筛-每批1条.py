# HiAgent「限定 CP 产品并过滤一级目录」完整节点代码。可全选替换。
# 仅包含此节点使用的函数，运行时直接加载。

import asyncio


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


from copy import deepcopy


IDENTITY=('bsnEvtId','userNo','taskId')


def _load(value):
    return json.loads(value) if isinstance(value,str) else deepcopy(value)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _string(value, label, nonblank=False):
    _require(isinstance(value,str) and (not nonblank or bool(value.strip())), label+' 必须是'+('非空' if nonblank else '')+'字符串')
    return value


def resolve_context(approval,business):
    """Use the current high-code fact resolver without cross-task shared state."""
    adapted=deepcopy(approval)
    for fact in adapted['facts']:
        fact['page_number']=1  # Whole-document text from the existing Dify extractor.
    return _resolve_material_facts(adapted,business)


import re


from datetime import datetime, timedelta, timezone


CALLBACK_ZONE = timezone(timedelta(hours=8))


CALLBACK_ITEM_FIELDS = ('materialName', 'sourceSystem', 'policyBasis', 'publishDate', 'publishDepartment')


def callback_time(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CALLBACK_ZONE)
    return parsed.astimezone(CALLBACK_ZONE)


def _callback_iso(value):
    return value.replace(tzinfo=None).isoformat(timespec='seconds')


def _callback_publish_date(value, completed):
    text = value.strip().rstrip('。.;； ') if isinstance(value, str) else ''
    try:
        return _callback_iso(callback_time(text))
    except ValueError:
        pass
    normalized = re.sub(r'\s+', '', text).replace('年', '-').replace('月', '-').replace('日', '')
    normalized = normalized.replace('/', '-').replace('.', '-').rstrip('-')
    match = re.fullmatch(r'(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?', normalized)
    if match:
        year, month, day = match.groups()
        try:
            return _callback_iso(datetime(int(year), int(month or 1), int(day or 1)))
        except ValueError:
            pass
    return _callback_iso(completed.replace(hour=0, minute=0, second=0, microsecond=0))


def build_callback(task_id, status, completed_at, materials=(), error_msg=''):
    completed = callback_time(completed_at)
    if status not in ('DONE', 'FAILED'):
        raise ValueError('无效的回调终态')
    items = []
    if status == 'DONE':
        for material in materials:
            item = {key: material[key] for key in CALLBACK_ITEM_FIELDS}
            item['publishDate'] = _callback_publish_date(item['publishDate'], completed)
            items.append(item)
    return dict(taskId=task_id, taskStatus=status, generatedAt=_callback_iso(completed),
                errorMsg=error_msg, checkListItems=items)


import json


from datetime import datetime, timezone


def matches_stage(title, stage):
    requested = ''.join(stage.split()).replace('放贷', '放款').split('、')
    directory = [word for word in ('放款', '签约') if word in title]
    return not directory or any(word in requested for word in directory)


def _refactor_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _refactor_bundle(document, selected, product, stage, phrases):
    filename = selected['fileName']
    _require(document['fileName'] == filename, '返回的文件名不一致')
    content = _string(document['content'], 'Word 正文', True)
    categories = []

    def collect(node, parent):
        path = parent + [dict(categoryId=node['categoryId'], categoryName=node['categoryName'])]
        categories.append(path)
        for child in node['children']:
            collect(child, path)

    for root in document['categories']:
        if matches_stage(root['categoryName'], stage):
            collect(root, [])
    retained = {(path[0]['categoryId'], path[0]['categoryName']) for path in categories}
    offset = 0
    parts = []
    for section in document['sections']:
        start, end = section['start'], section['end']
        _require(type(start) is int and type(end) is int and start == offset < end <= len(content),
                 '清单正文区间不完整')
        if section['categoryId'] is None or (section['categoryId'], section['categoryName']) in retained:
            parts.append(content[start:end])
        offset = end
    _require(offset == len(content), '清单正文区间不完整')

    rules = []
    for source in document['rules']:
        root = source['groups'][0] if source['groups'] else None
        if root and (root['sourceRuleId'], root['title']) not in retained:
            continue
        _require(source['productName'] == selected['productName'], '规则产品不一致')
        rules.append(dict(source, productName=product,
                          ruleKey=_refactor_json([product, source['sourcePosition']])))
    _require(len(rules) == len({rule['ruleKey'] for rule in rules}), '清单规则重复')
    return dict(documents=[dict(name=product, filename=filename, content=''.join(parts))],
                rules=rules, categories={product: categories}, phraseLibrary=phrases)


def _refactor_failure(business, message):
    result = dict(**{key: business.get(key, '') for key in IDENTITY}, status='FAILED', reason=message)
    return result, build_callback(result['taskId'], 'FAILED', datetime.now(timezone.utc), error_msg=message)


def _model_rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


OBJECT_TYPES = ('自然人', '机构', '不动产', '动产', '权利')
WORD_BATCH_SIZE = 1


def _refactor_approval(value, text, cp_name, checklist_name):
    try:
        model = _load(value)
    except ValueError:
        model = {}
    if not isinstance(model, dict):
        model = {}

    products = _model_rows(model.get('products'))
    matched = [row for row in products if row.get('productName') == cp_name]
    if not matched:
        matched = [row for row in products if row.get('productName') == checklist_name]

    origin = matched[0]['productName'] if matched else cp_name
    extracted = origin
    product_source = ''
    credit_records = []
    if matched:
        raw_name = matched[0].get('extractedProductName')
        if isinstance(raw_name, str) and raw_name.strip() == origin:
            extracted = raw_name
        source = matched[0].get('source')
        if isinstance(source, str):
            product_source = source
        for row in matched:
            records = row.get('creditRecords', row.get('conditions', []))
            if isinstance(records, list):
                credit_records.extend(records)

    facts = []
    for row in _model_rows(model.get('facts')):
        product_scope = row.get('productName')
        if product_scope not in ('', origin):
            continue
        name = row.get('name')
        subject = row.get('subject', '')
        if not isinstance(name, str) or not name.strip() or not isinstance(subject, str):
            continue

        present = row.get('present', True)
        negated = row.get('negated', False)
        if type(present) is not bool or type(negated) is not bool:
            continue

        fact_value = row.get('value', '')
        source = row.get('source', '')
        if type(fact_value) in (int, float):
            fact_value = str(fact_value)
        if not isinstance(fact_value, str) or not isinstance(source, str):
            continue
        if present and (not fact_value.strip() or not source.strip()):
            continue
        if not present and (fact_value or source or negated):
            continue

        submitted_types = row.get('objectTypes')
        object_types = []
        if present and not negated and source in text and isinstance(submitted_types, list):
            object_types = [kind for kind in OBJECT_TYPES if kind in submitted_types]
        facts.append(dict(name=name, value=fact_value, productName=cp_name if product_scope else '',
                          subject=subject, present=present, negated=negated, source=source,
                          page_number=1, objectTypes=object_types))

    target = dict(productName=cp_name, extractedProductName=extracted,
                  creditRecords=credit_records, source=product_source)
    return dict(text=text, products=[target], facts=facts,
                pages=[dict(page_number=1, text=text, tables=[])],
                productNameSource='PDF' if matched else 'CP')


def dify_main(state, value, body, status, phrases):
    data = _load(state) if state else {}
    business = data.get('businessData', {})
    out = dict(ok='yes', needs_match='no', request='', batches=[], answer='', result={}, callbackResult={},
               **{key: '' for key in (*IDENTITY, 'aspdNm', 'prcsStage', 'replyImageUrl')})

    if isinstance(status, str) and status.strip() == '200':
        status = 200

    try:
        _require(status == 200, 'Word 文件读取失败')
        data['approval'] = _refactor_approval(value, data.pop('markdown'), business['aspdNm'],
                                              data['selected']['productName'])
        approval_types = set()
        for fact in data['approval']['facts']:
            approval_types.update(fact.pop('objectTypes'))
        data['bundle'] = _refactor_bundle(_load(body), data['selected'], business['aspdNm'],
                                          business['prcsStage'], _load(phrases))
        data['context'] = resolve_context(data['approval'], business)

        bundle = data['bundle']
        keys = []
        for rule in bundle['rules']:
            applicable_types = rule.get('applicableObjectTypes', ['通用'])
            if '通用' in applicable_types or approval_types.intersection(applicable_types):
                keys.append(rule['ruleKey'])
        phrase_rules = bundle['phraseLibrary']['rules']
        out['batches'] = [_refactor_json(keys[i:i + WORD_BATCH_SIZE])
                          for i in range(0, len(keys), WORD_BATCH_SIZE)]
        out['answer'] = _refactor_json(dict(
            approvalObjectTypes=[kind for kind in OBJECT_TYPES if kind in approval_types],
            wordCandidateCount=len(keys), wordTypeFilteredCount=len(bundle['rules']) - len(keys),
        ))
        out['request'] = _refactor_json(dict(
            businessData=business, approval=data['approval'], checklists=bundle['documents'],
            rules=bundle['rules'], resolvedContext=data['context'], phraseRules=phrase_rules,
            categoryCatalog=bundle['categories'], requestedRowKeys=[row['rowKey'] for row in phrase_rules],
        ))
    except Exception:
        out['ok'] = 'no'
        out['result'], out['callbackResult'] = _refactor_failure(business, '批复结构化或 Word 清单校验失败')
    out['state'] = _refactor_json(data)
    return out


def handler(params):
    return dify_main(state=params.get('state'), value=params.get('value'), body=params.get('body'), status=params.get('status'), phrases=params.get('phrases'))
