"""Pure Python business runtime embedded verbatim in exported Dify code nodes."""
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit, quote, urlencode

from source_helpers import (ALIASES, _resolve_material_facts, validate_material_decisions,
    matches_business_stage, rule_category_path, select_category, iter_materials,
    group_materials, display_metadata, material_name, _directory_exclusion, _excluded_decision,
    validate_phrase_materials, merge_phrase_materials)


STRING_FIELDS = set('bsnEvtId userNo taskId contractNum replyId cstId cstNm ctrCharClcd ctrCharClNm '
    'contractNatureCode contractNatureNm aspdId aspdNm grtstlRcmbCd grtstlRcmbNm primGrtstl '
    'primGrtstlNm lwrsInd lwrsIndNm cprsvPdPoolInd cprsvPdPoolIndNm replyImageUrl expdtrHpnChar '
    'expdtrHpnNm expdtrCharCd expdtrCharNm ccy ctrTamt crlnFndsPymdCd crlnFndsPymdNm '
    'payOutAspdId payOutAspdNm'.split())
SUB_FIELDS=set('subcontractNum gnrNm gnrId frmCtrTpcd frmCtrTpNm'.split())
IDENTITY=('bsnEvtId','userNo','taskId')


def _load(value):
    return json.loads(value) if isinstance(value,str) else deepcopy(value)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _string(value, label, nonblank=False):
    _require(isinstance(value,str) and (not nonblank or bool(value.strip())), label+' 必须是'+('非空' if nonblank else '')+'字符串')
    return value


def validate_business(value):
    data=_load(value)
    _require(isinstance(data,dict),'businessData 必须是 JSON 对象')

    def clean(obj, strings, nested):
        _require(isinstance(obj,dict),'业务对象必须是 JSON 对象')
        result=deepcopy(obj)
        for key in strings|set(nested):
            if key not in result:
                continue
            if result[key] is None and key not in IDENTITY:
                result.pop(key)
            elif key in strings:
                _string(result[key],key)
            else:
                _require(isinstance(result[key],list),key+' 必须是数组')
                result[key]=[clean(item,*nested[key]) for item in result[key]]
        return result

    data=clean(data,STRING_FIELDS,{'subcontracts':(SUB_FIELDS,{
        'jntGnrInfs':(set('cstId cstNm idCardNo'.split()),{}),
        'SubcontractCollateralRels':(set('cltlId cltlTp cltlNm'.split()),{})})})
    for key in IDENTITY:
        data[key]=_string(data.get(key),key,True).strip()
    if 'ctrTamt' in data:
        _require(re.fullmatch(r'^$|^(0|[1-9][0-9]*)(\.[0-9]+)?$',data['ctrTamt']) is not None,'ctrTamt 必须是十进制字符串')
    url=_string(data.get('replyImageUrl'),'replyImageUrl',True)
    validate_pdf_source(url)
    return data


class WorkflowBusinessError(ValueError):
    """Only explicitly selected safe business messages enter failure results."""


def validate_pdf_source(value):
    if value.lower().startswith(('http://','https://')):
        parsed=urlsplit(value)
        _require(parsed.scheme in ('http','https') and bool(parsed.hostname) and not parsed.username
                 and not parsed.password and not parsed.fragment and not any(c.isspace() for c in value),
                 'replyImageUrl 必须是有效 HTTP/HTTPS 地址')
        parsed.port
    else:
        _require(not value.startswith('//') and '://' not in value and '\\' not in value
                 and not any(ord(c)<32 or ord(c)==127 for c in value),'影像相对路径无效')
        parts=value.removeprefix('/').split('/')
        _require(len(parts)>=5 and all(p.strip() and p not in ('.','..') for p in parts),'影像相对路径无效')
    return value


def resolve_pdf_url(value,base='',au=''):
    validate_pdf_source(value)
    if value.lower().startswith(('http://','https://')):
        return value
    if not base or not au.strip():
        raise WorkflowBusinessError('影像相对路径需要在 Dify 配置下载基础地址和认证参数')
    parsed=urlsplit(base)
    _require(parsed.scheme in ('http','https') and parsed.hostname and not parsed.query
             and not parsed.fragment and not parsed.username and not parsed.password,'下载基础地址无效')
    parsed.port
    relative='/'.join(quote(part,safe='') for part in value.removeprefix('/').split('/'))
    return base.rstrip('/')+'/'+relative+'?'+urlencode({'au':au})


def _layout_index(text):
    """Index layout-insensitive characters, retaining ASCII token boundaries."""
    chars,positions=[],[]
    whitespace=None
    for index,char in enumerate(text):
        if char.isspace():
            if whitespace is None:
                whitespace=index
            continue
        if whitespace is not None and chars:
            previous=chars[-1]
            if previous.isascii() and previous.isalnum() and char.isascii() and char.isalnum():
                chars.append(' ')
                positions.append(whitespace)
        chars.append(char)
        positions.append(index)
        whitespace=None
    return ''.join(chars),positions


def _restore_quote(source,text,index,label):
    """Return an exact original span; never substitute non-whitespace content."""
    if source in text:
        return source
    needle,_=_layout_index(source)
    normalized,positions=index
    start=normalized.find(needle) if needle else -1
    _require(start>=0,label)
    return text[positions[start]:positions[start+len(needle)-1]+1]


def validate_approval(value, text, catalog, *, allow_empty=False):
    data=_load(value)
    _require(isinstance(data,dict) and isinstance(data.get('products'),list)
             and (allow_empty or data['products']),'未识别到可售产品')
    _string(text,'批复正文',True)
    layout=_layout_index(text)
    result={'text':text,'products':[],'facts':[]}
    products={}
    for product in data['products']:
        _require(isinstance(product,dict),'产品结构不合法')
        name=_string(product.get('productName'),'productName',True).strip()
        raw=_string(product.get('extractedProductName',name),'extractedProductName',True)
        quote=_string(product.get('source'),'产品引文',True)
        _require(raw.strip()==name,'产品名称必须严格一致，不允许语义改名')
        quote=_restore_quote(quote,text,layout,'产品引文无法在批复中核对')
        _require(_layout_index(raw)[0] in _layout_index(quote)[0],'产品引文无法在批复中核对')
        if name not in products:
            products[name]=dict(extractedProductName=raw,productName=name,source=quote,creditRecords=[])
            result['products'].append(products[name])
        records=product.get('creditRecords',product.get('conditions',[]))
        _require(isinstance(records,list),'授信条件必须是数组')
        products[name]['creditRecords'].extend(deepcopy(records))
    facts=data.get('facts',[])
    _require(isinstance(facts,list),'facts 必须是数组')
    for f in facts:
        _require(isinstance(f,dict),'事实结构不合法')
        fact=dict(name=_string(f.get('name'),'事实名称',True),value=_string(f.get('value',''),'事实值'),
            productName=_string(f.get('productName',''),'事实产品'),subject=_string(f.get('subject',''),'事实主体'),
            present=f.get('present',True),negated=f.get('negated',False),
            source=_string(f.get('source',''),'事实引文'),page_number=1)
        _require(type(fact['present']) is bool and type(fact['negated']) is bool,'事实状态必须是布尔值')
        _require(not fact['productName'] or fact['productName'] in products,'事实引用未知产品')
        if fact['present']:
            _require(bool(fact['value'].strip()) and bool(fact['source'].strip()),'事实引文不存在')
            fact['source']=_restore_quote(fact['source'],text,layout,'事实引文不存在')
        else:
            _require(not fact['value'] and not fact['source'] and not fact['negated'],'未知事实不能伪造引文或否定')
        result['facts'].append(fact)
    # page_number=1 is a virtual whole-document scope, never presented as a real PDF page.
    result['pages']=[dict(page_number=1,text=text,tables=[])]
    return result


def validate_product_matches(value,approval,catalog):
    """Exact names never call the model; semantic choices only select rule files."""
    names=[p['productName'] for p in approval['products']]
    _require(names and len(names)==len(set(names)),'批复产品集合无效')
    _require(isinstance(catalog,list) and catalog and
             all(isinstance(n,str) and n.strip() for n in catalog) and
             len(catalog)==len(set(catalog)),'产品目录不可用')
    selected={n:dict(extractedProductName=n,checklistProductName=n,method='EXACT')
              for n in names if n in catalog}
    missing=[n for n in names if n not in selected]
    data=_load(value)
    _require(isinstance(data,dict) and set(data)=={'matches'},'产品匹配必须返回 matches 对象')
    rows=data['matches']
    _require(isinstance(rows,list) and len(rows)==len(missing),'产品匹配遗漏或重复')
    found={}
    for row in rows:
        _require(isinstance(row,dict) and set(row)=={'extractedProductName','checklistProductName'},
                 '产品匹配字段不合法')
        original=_string(row['extractedProductName'],'批复产品名',True)
        target=_string(row['checklistProductName'],'材料清单产品名')
        _require(original in missing and original not in found,'产品匹配重复、改名或覆盖完全匹配')
        _require(not target or target in catalog,'模型选择了目录外的材料清单')
        found[original]=dict(row,method='MODEL')
    selected.update(found)
    return [selected[n] for n in names]


def bind_product_bundle(bundle,approval,matches):
    """Rebind selected templates to PDF names, just as the original pipeline does."""
    names=[p['productName'] for p in approval['products']]
    _require([m['extractedProductName'] for m in matches]==names,'清单匹配产品集合或顺序不完整')
    selected=list(dict.fromkeys(m['checklistProductName'] for m in matches))
    validate_bundle(bundle,dict(products=[dict(productName=n) for n in selected]))
    docs={d['name']:d for d in bundle['documents']}
    result=deepcopy(bundle)
    result.update(documents=[],rules=[],categories={})
    for match in matches:
        original,target=match['extractedProductName'],match['checklistProductName']
        result['documents'].append(dict(deepcopy(docs[target]),name=original))
        result['categories'][original]=deepcopy(bundle['categories'][target])
        for rule in bundle['rules']:
            if rule['productName']!=target:
                continue
            rule=deepcopy(rule)
            if original!=target:
                key=json.loads(rule['ruleKey'])
                _require(isinstance(key,list) and len(key)==2 and key[0]==target,
                         '清单规则键与匹配产品不一致')
                rule['ruleKey']=json.dumps([original,key[1]],ensure_ascii=False,separators=(',',':'))
            rule['productName']=original
            result['rules'].append(rule)
    return validate_bundle(result,approval)


def resolve_context(approval,business):
    """Use the current high-code fact resolver without cross-task shared state."""
    adapted=deepcopy(approval)
    for fact in adapted['facts']:
        fact['page_number']=1  # Whole-document text from the existing Dify extractor.
    return _resolve_material_facts(adapted,business)



def validate_bundle(bundle,approval):
    _require(isinstance(bundle,dict),'规则服务没有返回对象')
    names=[p['productName'] for p in approval['products']]
    docs=bundle.get('documents',[])
    _require([d['name'] for d in docs]==names,'规则服务产品集合或顺序不完整')
    _require(isinstance(bundle.get('rules'),list) and bundle['rules'],'规则清单为空')
    _require(len({r['ruleKey'] for r in bundle['rules']})==len(bundle['rules']),'原规则编号重复')
    _require(set(r['productName'] for r in bundle['rules'])==set(names),'规则产品不完整')
    _require(all(re.fullmatch(r'[0-9a-f]{64}',d['content_hash']) and d['content'].strip() for d in docs),'清单内容或哈希无效')
    _require(bundle.get('phraseLibrary',{}).get('rules'),'话术库缺失或不完整')
    _require(set(bundle.get('categories',{}))==set(names),'目录不完整')
    return bundle


def validate_word(value,bundle,context,business):
    decisions=_load(value)
    decisions=decisions['decisions'] if isinstance(decisions,dict) else decisions
    try:
        return validate_material_decisions(decisions,bundle['rules'],context,business_data=business)
    except Exception as error:
        raise ValueError('Word 材料决定不完整或来源/对象校验失败') from error


def plan_word_batches(rules,business):
    """Match the original generator: exclude other directories, then batch by 20."""
    eligible=[]
    excluded=[]
    for rule in rules:
        if _directory_exclusion(rule,business):
            excluded.append(_excluded_decision(rule,'一级目录与 grtstlRcmbNm 不匹配，整目录排除。'))
        else:
            eligible.append(rule['ruleKey'])
    return dict(batches=[eligible[i:i+20] for i in range(0,len(eligible),20)],
                preExcluded=excluded,index=0,decisions=[],failed=False)


def word_request(data,keys):
    return dict(businessData=data['businessData'],approval=data['approval'],
                checklists=data['bundle']['documents'],rules=data['bundle']['rules'],
                resolvedContext=data['context'],requestedRuleKeys=keys)


def complete_word_batches(data):
    plan=data['wordPlan']
    _require(not plan['failed'] and plan['index']==len(plan['batches']),'Word 批次尚未完整通过')
    decisions=validate_word(dict(decisions=plan['preExcluded']+plan['decisions']),
                            data['bundle'],data['context'],data['businessData'])
    return json.dumps(dict(decisions=decisions),ensure_ascii=False,separators=(',',':'))


def finish(business,approval,bundle,context,word,phrase_value,*,stage_business=None):
    """Reuse current high-code phrase validation/merging; gateway owns itemId."""
    phrase=_load(phrase_value)
    if isinstance(phrase,dict):
        _require(set(phrase)=={'decisions'},'话术必须返回 decisions 对象')
        phrase=phrase['decisions']
    _require(isinstance(phrase,list),'话术必须返回决定数组')
    # The existing Dify document extractor supplies whole-document text, not real pages.
    # This internal page 1 only supports continuous-quote checks; it is not PDF pagination.
    cited=dict(approval,pages=[dict(page_number=1,text=approval['text'],tables=[])])
    validated=validate_phrase_materials(phrase,bundle['phraseLibrary']['rules'],cited,
        bundle['rules'],context,category_catalog=bundle['categories'])
    rules,decisions=merge_phrase_materials(bundle['rules'],word,validated,bundle['phraseLibrary'],
        category_catalog=bundle['categories'],business_data=business if stage_business is None else stage_business)
    by_key={d['ruleKey']:d for d in decisions}
    docs={d['name']:d for d in bundle['documents']}
    located=[]
    for product in approval['products']:
        name=product['productName']
        for rule in rules:
            if rule['productName']!=name:
                continue
            decision=by_key[rule['ruleKey']]
            if decision['action']!='KEEP':
                continue
            for instance in decision['instances']:
                metadata=display_metadata(instance,rule['cells'][4])
                item=dict(itemType='MATERIAL',productName=name,materialName=material_name(name,instance),
                    applicability='APPLICABLE',requirement=instance['requirement'],**metadata)
                located.append((item,rule_category_path(rule)))
    products=[dict(extractedProductName=p['extractedProductName'],productName=p['productName'],
        checklistFileName=docs[p['productName']]['filename']) for p in approval['products']]
    items=group_materials(products,located,bundle['categories'])
    total=len(list(iter_materials(items)))
    _require(total>0,'没有可输出材料')
    return dict(**{k:business[k] for k in IDENTITY},message='生成完成',
        data=dict(generatedAt=datetime.now(timezone.utc).isoformat(),total=total,products=products,items=items))



def failure(business,stage):
    return dict(**{k:business.get(k,'') for k in IDENTITY},status='FAILED',reason='处理失败：'+stage)


def node_main(step,state='{}',value='',body='',businessData='',download_base_url='',image_au=''):
    """Stable code-node interface: outputs never leak provider bodies or credentials."""
    data=_load(state) if state else {}
    request={}
    result={}
    url=''
    needs_match='no'
    has_batch='no'
    done='no'
    answer=''
    try:
        if step=='input':
            data={'businessData':validate_business(businessData)}
            url=resolve_pdf_url(data['businessData']['replyImageUrl'],download_base_url,image_au)
        elif step=='check_file':
            files=_load(value)
            _require(isinstance(files,list) and len(files)==1 and isinstance(files[0],dict),'文件响应必须包含一个 PDF')
            f=files[0]
            _require(str(f.get('extension','')).lower().lstrip('.')=='pdf','文件实际类型不是 PDF')
            _require(type(f.get('size')) is int and 0<f['size']<=10*1024*1024,'PDF 为空或超过 10 MiB')
        elif step=='prepare_extract':
            catalog=_load(body)
            _require(isinstance(catalog,dict) and isinstance(catalog.get('products'),list),'产品目录不可用')
            data['catalog']=catalog['products']
            _require(data['catalog'] and all(isinstance(n,str) and n.strip() for n in data['catalog'])
                     and len(data['catalog'])==len(set(data['catalog'])),'产品目录不可用')
            data['text']=_string(value,'批复正文',True)
            request=dict(businessData=data['businessData'],productNames=data['catalog'],text=data['text'])
        elif step=='validate_extract':
            data['approval']=validate_approval(value,data['text'],data['catalog'])
            names=[p['productName'] for p in data['approval']['products']]
            missing=[n for n in names if n not in data['catalog']]
            if missing:
                needs_match='yes'
                request=dict(extractedProducts=missing,catalog=data['catalog'])
            else:
                data['productMatches']=validate_product_matches(dict(matches=[]),data['approval'],data['catalog'])
                request=dict(products=names)
        elif step=='validate_matches':
            data['productMatches']=validate_product_matches(value,data['approval'],data['catalog'])
            matches=[m for m in data['productMatches'] if m['checklistProductName']]
            if not matches:
                raise WorkflowBusinessError('任务处理失败：所有产品均未匹配到唯一有效的参考材料清单，已全部跳过')
            active={m['extractedProductName'] for m in matches}
            data['originalApproval']=deepcopy(data['approval'])
            data['approval']=dict(data['approval'],
                products=[p for p in data['approval']['products'] if p['productName'] in active],
                facts=[f for f in data['approval']['facts'] if not f.get('productName') or f['productName'] in active])
            request=dict(products=list(dict.fromkeys(m['checklistProductName'] for m in matches)))
        elif step=='prepare_word':
            bundle=_load(body)
            data['bundle']=(bind_product_bundle(bundle,data['approval'],[m for m in data['productMatches'] if m['checklistProductName']])
                            if 'productMatches' in data else validate_bundle(bundle,data['approval']))
            data['context']=resolve_context(data['approval'],data['businessData'])
            data['wordPlan']=plan_word_batches(data['bundle']['rules'],data['businessData'])
            request=word_request(data,[r['ruleKey'] for r in data['bundle']['rules']])
        elif step=='prepare_word_batch':
            plan=data['wordPlan']
            _require(not plan['failed'],'Word 批次已失败，不能继续')
            _require(type(plan['index']) is int and 0<=plan['index']<=len(plan['batches']),'Word 批次序号无效')
            if plan['index']==len(plan['batches']):
                answer=complete_word_batches(data)
                done='yes'
            else:
                keys=plan['batches'][plan['index']]
                _require(0<len(keys)<=20,'Word 每批必须为1至20条')
                request=word_request(data,keys)
                has_batch='yes'
        elif step=='consume_word_batch':
            plan=data['wordPlan']
            _require(not plan['failed'] and 0<=plan['index']<len(plan['batches']),'Word 当前批次无效')
            if value!='':
                reply=_load(value)
                _require(isinstance(reply,dict) and set(reply)=={'decisions'},'Word 批次输出必须仅含 decisions')
                keys=plan['batches'][plan['index']]
                selected={k for k in keys}
                rules=[r for r in data['bundle']['rules'] if r['ruleKey'] in selected]
                _require(len(rules)==len(keys),'Word 批次规则集合无效')
                validated=validate_material_decisions(reply['decisions'],rules,data['context'],business_data=data['businessData'])
                plan['decisions'].extend(validated)
                plan['index']+=1
                if plan['index']==len(plan['batches']):
                    answer=complete_word_batches(data)
                    done='yes'
        elif step=='validate_word':
            data['word']=validate_word(value,data['bundle'],data['context'],data['businessData'])
            request=dict(businessData=data['businessData'],approval=dict(data['approval'],pages=[dict(page_number=1,text=data['approval']['text'],tables=[])]),checklists=data['bundle']['documents'],
                rules=data['bundle']['rules'],resolvedContext=data['context'],
                phraseRules=data['bundle']['phraseLibrary']['rules'],categoryCatalog=data['bundle']['categories'],
                requestedRowKeys=[r['rowKey'] for r in data['bundle']['phraseLibrary']['rules']])
        elif step=='finalize':
            result=finish(data['businessData'],data['approval'],data['bundle'],data['context'],data['word'],value)
        elif step=='transport_error':
            raise ValueError('外部请求或解析节点失败')
    except Exception as error:
        result=failure(data.get('businessData',{}),step)
        if isinstance(error,WorkflowBusinessError):
            result['reason']=str(error)
        if step in ('prepare_word_batch','consume_word_batch'):
            if 'wordPlan' in data:
                data['wordPlan']['failed']=True
            done='yes'
            answer=json.dumps(dict(__error__='Word 批次处理失败'),ensure_ascii=False)
        return dict(state=json.dumps(data,ensure_ascii=False,separators=(',',':')),request='',url='',ok='no',
                    needs_match='no',has_batch='no',done=done,answer=answer,result=result)
    return dict(state=json.dumps(data,ensure_ascii=False,separators=(',',':')),
                request=json.dumps(request,ensure_ascii=False,separators=(',',':')),url=url,ok='yes',
                needs_match=needs_match,has_batch=has_batch,done=done,answer=answer,result=result)
