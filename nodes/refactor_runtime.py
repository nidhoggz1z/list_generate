"""CP-scoped checklist workflow. Bundled with runtime.py by the DSL builder."""
import json
from datetime import datetime, timezone

from gateway.result_format import build_callback
from runtime import (IDENTITY, _load, _require, _string, finish, iter_materials,
                     node_main, resolve_context, resolve_pdf_url, validate_approval,
                     validate_business, validate_product_matches, validate_word)


def matches_stage(title, stage):
    requested = ''.join(stage.split()).replace('放贷', '放款').split('、')
    directory = [word for word in ('放款', '签约') if word in title]
    return not directory or any(word in requested for word in directory)


def _refactor_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _refactor_approval(value, text, cp_name, checklist_name):
    approval = validate_approval(value, text, [], allow_empty=True)
    target = next((p for p in approval['products'] if p['productName'] == cp_name), None)
    if target is None:
        target = next((p for p in approval['products'] if p['productName'] == checklist_name), None)
    origin = target['productName'] if target else None
    approval['facts'] = [
        dict(fact, productName=cp_name if fact['productName'] else '')
        for fact in approval['facts'] if not fact['productName'] or fact['productName'] == origin
    ]
    approval['products'] = [dict(productName=cp_name,
                                 extractedProductName=target['extractedProductName'] if target else cp_name,
                                 creditRecords=target['creditRecords'] if target else [],
                                 source=target['source'] if target else '')]
    approval['productNameSource'] = 'PDF' if target else 'CP'
    return approval


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


def _refactor_word_request(data, keys):
    selected = set(keys)
    rules = [rule for rule in data['bundle']['rules'] if rule['ruleKey'] in selected]
    _require(len(rules) == len(keys) <= 20, 'Word 批次规则无效')
    return dict(businessData=data['businessData'], approval=data['approval'],
                checklists=data['bundle']['documents'], rules=rules, resolvedContext=data['context'],
                requestedRuleKeys=keys)


def _refactor_failure(business, message):
    result = dict(**{key: business.get(key, '') for key in IDENTITY}, status='FAILED', reason=message)
    return result, build_callback(result['taskId'], 'FAILED', datetime.now(timezone.utc), error_msg=message)


def refactor_node(step, state='', value='', body='', businessData='', download_base_url='',
                  image_au='', phrases=None, words=None, status=200):
    data = _load(state) if state else {}
    business = data.get('businessData', {})
    out = dict(ok='yes', needs_match='no', request='', batches=[], answer='', result={}, callbackResult={},
               **{key: '' for key in (*IDENTITY, 'aspdNm', 'prcsStage', 'replyImageUrl')})
    errors = {
        'input': 'CP 参数或批复影像地址无效', 'check_file': '批复下载失败或 PDF 格式及大小无效',
        'prepare_match': '材料清单目录服务不可用，请检查地址、鉴权及云端网络',
        'select_file': '目标产品未匹配到唯一有效的材料清单',
        'prepare_extract': '批复提取失败或 Markdown 为空', 'prepare_analysis': '批复结构化或 Word 清单校验失败',
        'word_prepare': 'Word 批次输入无效', 'word_validate': 'Word 批次分析结果无效',
        'finalize': 'Word 或话术分析未完整通过', 'transport': '外部请求或批复提取失败',
        'catalog_error': '无法连接材料清单目录服务，请检查 CHECKLIST_GATEWAY_URL 及云端网络',
        'rules_error': '无法连接 Word 正文服务，请检查文件服务地址及网络',
        'download_error': '无法下载批复 PDF，请检查 replyImageUrl 是否能从 Dify 访问',
    }
    try:
        if step == 'input':
            try:
                incoming = _load(businessData)
                if isinstance(incoming, dict):
                    business = {key: incoming[key].strip() for key in IDENTITY
                                if isinstance(incoming.get(key), str)}
            except (ValueError, TypeError):
                pass
            business = validate_business(businessData)
            for key in ('aspdNm', 'prcsStage'):
                business[key] = _string(business.get(key), key, True).strip()
            data['businessData'] = business
            out.update({key: business[key] for key in (*IDENTITY, 'aspdNm', 'prcsStage')})
            out['replyImageUrl'] = resolve_pdf_url(business['replyImageUrl'], download_base_url, image_au)
        elif step == 'check_file':
            _require(200 <= status < 300 and node_main('check_file', value=value)['ok'] == 'yes',
                     'PDF 校验失败')
        elif step == 'prepare_match':
            _require(status == 200, '目录读取失败')
            rows = _load(body)['files']
            names = [_string(row['productName'], '产品名', True) for row in rows]
            files = [_string(row['fileName'], '文件名', True) for row in rows]
            _require(names and len(names) == len(set(names)) == len(set(files)), '目录重复或为空')
            data['files'] = dict(zip(names, files))
            if business['aspdNm'] not in data['files']:
                out['needs_match'] = 'yes'
                out['request'] = _refactor_json(dict(extractedProducts=[business['aspdNm']], catalog=names))
        elif step == 'select_file':
            product = business['aspdNm']
            matches = validate_product_matches(value or {'matches': []},
                                               {'products': [{'productName': product}]}, list(data['files']))
            name = matches[0]['checklistProductName']
            _require(name in data['files'], '没有唯一材料清单')
            data['selected'] = dict(productName=name, fileName=data['files'][name])
            out['request'] = _refactor_json(dict(fileName=data['files'][name]))
        elif step == 'prepare_extract':
            if isinstance(value, list):
                _require(len(value) == 1, '批复提取结果缺失或重复')
                value = value[0]
            data['markdown'] = _string(value, '批复 Markdown', True)
            out['request'] = _refactor_json(dict(businessData=business, text=data['markdown'],
                                                  targetProduct=business['aspdNm'],
                                                  checklistProduct=data['selected']['productName']))
        elif step == 'prepare_analysis':
            _require(status == 200, 'Word 文件读取失败')
            data['approval'] = _refactor_approval(value, data.pop('markdown'), business['aspdNm'],
                                                  data['selected']['productName'])
            data['bundle'] = _refactor_bundle(_load(body), data['selected'], business['aspdNm'],
                                              business['prcsStage'], _load(phrases))
            data['context'] = resolve_context(data['approval'], business)
            keys = [r['ruleKey'] for r in data['bundle']['rules']]
            out['batches'] = [_refactor_json(keys[i:i + 20]) for i in range(0, len(keys), 20)]
            out['request'] = _refactor_json(dict(businessData=business, approval=data['approval'],
                checklists=data['bundle']['documents'], rules=data['bundle']['rules'], resolvedContext=data['context'],
                phraseRules=data['bundle']['phraseLibrary']['rules'], categoryCatalog=data['bundle']['categories'],
                requestedRowKeys=[r['rowKey'] for r in data['bundle']['phraseLibrary']['rules']]))
        elif step == 'word_prepare':
            out['request'] = _refactor_json(_refactor_word_request(data, _load(value)))
        elif step == 'word_validate':
            keys = _load(body)
            request = _refactor_word_request(data, keys)
            validated = validate_word(value, {'rules': request['rules']}, data['context'], {})
            out['answer'] = _refactor_json(validated)
        elif step == 'finalize':
            decisions = [decision for batch in _load(words or []) for decision in _load(batch)]
            if data['bundle']['rules'] or decisions:
                validated = validate_word(decisions, data['bundle'], data['context'], {})
            else:
                validated = []
            try:
                result = finish(business, data['approval'], data['bundle'], data['context'], validated, value,
                                stage_business={})
            except ValueError as error:
                if str(error) == '没有可输出材料':
                    errors[step] = '未生成适用材料'
                raise
            out['result'] = result
            out['callbackResult'] = build_callback(business['taskId'], 'DONE', result['data']['generatedAt'],
                                                   iter_materials(result['data']['items']))
        else:
            raise ValueError('工作流外部节点异常')
    except Exception:
        out['ok'] = 'no'
        out['result'], out['callbackResult'] = _refactor_failure(business, errors[step])
    out['state'] = _refactor_json(data)
    return out
