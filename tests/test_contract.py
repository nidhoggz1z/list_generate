"""Business decisions, not a snapshot of generated implementation text."""
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nodes'))


def fixture():
    name = '产品甲'
    rules = []
    for index, (title, kind) in enumerate([('营业执照', 'MATERIAL'), ('担保人身份证明', 'MATERIAL'),
                                           ('签约合同', 'MATERIAL'), ('纯核查动作', 'CHECK')], 1):
        rules.append(dict(productName=name, ruleKey=f'w{index}', sourceRuleId=str(index),
            cells=[str(index), title, '', '明确适用时提供', ''], kind=kind,
            groups=[dict(sourceRuleId='2' if index == 3 else '1', title='签约资料' if index == 3 else '基本资料')]))
    categories = {name: [[dict(categoryId='1', categoryName='基本资料')],
                         [dict(categoryId='2', categoryName='签约资料')]]}
    phrase = dict(rowKey='row:2', rowNumber=2, phraseId='P1', phrase='须提供签约合同',
        materialText='1.1签约合同', materials=[dict(materialKey='row:2/item:1', name='签约合同',
                                                 context='1.1签约合同', sourceRuleId='话术库:行2:材料1')])
    bundle = dict(documents=[dict(name=name, filename=name+'材料清单.docx', content='完整清单',
        content_hash='a'*64)], rules=rules, categories=categories,
        phraseLibrary=dict(filename='话术库对应材料.xlsx', content_hash='b'*64, rules=[phrase]))
    business = dict(bsnEvtId=' B1 ', userNo='U1', taskId='T1', cstNm='借款企业',
        grtstlRcmbNm='放款', replyImageUrl='http://127.0.0.1/approval.pdf',
        subcontracts=[dict(gnrNm='甲'), dict(gnrNm='乙')])
    text = '可售产品：产品甲。借款人借款企业。须提供签约合同。'
    approval = dict(products=[dict(extractedProductName=name, productName=name, source='产品甲')],
                    facts=[], text=text)
    return business, approval, bundle


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('runtime'), 'business nodes not implemented')
        import runtime
        self.rt = runtime
        self.business, self.approval, self.bundle = fixture()

    def test_input_preserves_fields_but_requires_url_and_typed_nested_values(self):
        result = self.rt.validate_business(json.dumps(self.business))
        self.assertEqual(result['bsnEvtId'], 'B1')
        self.assertEqual(len(result['subcontracts']), 2)
        for update in [{'replyImageUrl': ''}, {'ctrTamt': 12}, {'subcontracts': [{} , 3]}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.rt.validate_business(self.business | update)

    def test_product_names_and_references_must_come_from_approval(self):
        self.rt.validate_approval(self.approval, self.approval['text'], ['产品甲'])
        bad = copy.deepcopy(self.approval)
        bad['products'][0]['source'] = '不存在的引文'
        with self.assertRaises(ValueError):
            self.rt.validate_approval(bad, self.approval['text'], ['产品甲'])

    def test_highcode_unscoped_json_objects_apply_to_products_and_pdf_overrides(self):
        approval = copy.deepcopy(self.approval)
        approval['products'].append(dict(extractedProductName='产品乙', productName='产品乙', source='产品乙'))
        business = self.business | dict(aspdNm='产品甲', payOutAspdNm='产品乙')
        context = self.rt.resolve_context(approval, business)
        names = [(o['productName'], o['name']) for o in context['objects'] if o['kind']=='guarantor']
        self.assertEqual(names, [('产品甲','甲'),('产品甲','乙'),('产品乙','甲'),('产品乙','乙')])
        approval['facts'] = [dict(name='客户名称', value='批复客户', productName='产品乙', subject='',
                                  present=True, negated=False, source='批复客户')]
        context = self.rt.resolve_context(approval, business)
        self.assertIn('批复客户', [o['name'] for o in context['objects']])

    def word_decisions(self, context):
        customer = next(o for o in context['objects'] if o['kind']=='customer')
        return [dict(productName='产品甲', ruleKey=r['ruleKey'],
            action='CHECK' if r['kind']=='CHECK' else 'KEEP' if r['ruleKey']=='w1' else 'EXCLUDE',
            applicability='APPLICABLE' if r['ruleKey']=='w1' else 'UNKNOWN',
            objectKinds=['customer'] if r['ruleKey']=='w1' else [], scopeObjectKey='',
            reason='按规则判断', evidence=[dict(type='RULE', ruleKey=r['ruleKey'], quote=r['cells'][1])],
            instances=[dict(objectKey=customer['objectKey'], subject=customer['name'], materialName=r['cells'][1],
                requirement='提供有效证照', reason='借款主体资料', evidence=[
                    dict(type='RULE', ruleKey=r['ruleKey'], quote=r['cells'][1]),
                    dict(type='OBJECT', objectKey=customer['objectKey'], source=customer['source'])])]
                if r['ruleKey']=='w1' else [], excludedObjects=[])
            for r in self.bundle['rules']]

    def test_word_rule_coverage_and_unknown_are_not_silently_kept(self):
        context = self.rt.resolve_context(self.approval, self.business)
        decisions = self.word_decisions(context)
        valid = self.rt.validate_word(decisions, self.bundle, context, self.business)
        self.assertEqual(sum(d['action']=='KEEP' for d in valid), 1)
        with self.assertRaises(ValueError):
            self.rt.validate_word(decisions[:-1], self.bundle, context, self.business)
        decisions[0]['applicability']='UNKNOWN'
        valid = self.rt.validate_word(decisions, self.bundle, context, self.business)
        self.assertEqual(valid[0]['action'], 'EXCLUDE')

    def phrase_decisions(self, word_key='w3', unknown=False):
        return [dict(rowKey='row:2', scope='EXPLICIT', matches=[dict(page_number=1,quote='须提供签约合同')],
            products=[dict(productName='产品甲', materials=[dict(materialKey='row:2/item:1',
                categoryPath=['2'], applicability='UNKNOWN' if unknown else 'APPLICABLE',
                instances=[dict(objectKey='', subject='', materialName='签约合同', requirement='提供合同',
                                wordRuleKey=word_key)] if not unknown else [])])])]

    def test_excel_cannot_restore_stage_excluded_directory_and_dify_omits_item_ids(self):
        context=self.rt.resolve_context(self.approval, self.business)
        word=self.rt.validate_word(self.word_decisions(context), self.bundle, context, self.business)
        result=self.rt.finish(self.business, self.approval, self.bundle, context, word, self.phrase_decisions())
        self.assertNotIn('status', result)
        self.assertEqual(result['data']['total'], 1)
        materials=list(self.rt.iter_materials(result['data']['items']))
        self.assertTrue(all('itemId' not in m for m in materials))
        self.assertEqual(materials[0]['publishDate'], '2026年')
        self.assertEqual(set(materials[0]), {'itemType','productName','materialName','sourceSystem',
            'policyBasis','publishDate','publishDepartment','applicability','requirement'})
        self.assertNotIn('sourceDocumentHash', result['data'])

    def test_excel_unknown_equivalence_is_discarded_and_fake_quote_fails(self):
        context=self.rt.resolve_context(self.approval, self.business)
        word=self.rt.validate_word(self.word_decisions(context), self.bundle, context, self.business)
        result=self.rt.finish(self.business,self.approval,self.bundle,context,word,self.phrase_decisions(unknown=True))
        self.assertEqual(result['data']['total'],1)
        bad=self.phrase_decisions()
        bad[0]['matches'][0]['quote']='不存在的引文'
        from source_helpers import PhraseMaterialError
        with self.assertRaises(PhraseMaterialError):
            self.rt.finish(self.business,self.approval,self.bundle,context,word,bad)

    def test_failed_stage_has_old_failure_contract_and_no_partial_data(self):
        state={'businessData':self.business, 'approval':self.approval,'bundle':self.bundle}
        outputs=self.rt.node_main('validate_word',state=json.dumps(state,ensure_ascii=False),value='not JSON')
        self.assertEqual(outputs['ok'],'no')
        self.assertEqual(outputs['result']['status'],'FAILED')
        self.assertNotIn('data',outputs['result'])

    def test_restored_word_material_keeps_original_rule_order(self):
        context=self.rt.resolve_context(self.approval,self.business)
        word=self.rt.validate_word(self.word_decisions(context),self.bundle,context,self.business)
        original=copy.deepcopy(word[0]['instances'])
        word[0].update(action='EXCLUDE',instances=[])
        word[1].update(action='KEEP',instances=original)
        word[1]['instances'][0]['materialName']='原第二条材料'
        result=self.rt.finish(self.business,self.approval,self.bundle,context,word,self.phrase_decisions(word_key='w1'))
        materials=list(self.rt.iter_materials(result['data']['items']))
        self.assertIn('营业执照',materials[0]['materialName'])

    def test_model_request_covers_every_word_rule(self):
        state=dict(businessData=self.business,approval=self.approval)
        output=self.rt.node_main('prepare_word',state=json.dumps(state,ensure_ascii=False),body=json.dumps(self.bundle,ensure_ascii=False))
        self.assertEqual(output['ok'],'yes')
        request=json.loads(output['request'])
        self.assertEqual(request['requestedRuleKeys'],[r['ruleKey'] for r in self.bundle['rules']])

    def test_downloaded_pdf_must_be_single_nonempty_pdf_within_limit(self):
        state=json.dumps({'businessData':self.business},ensure_ascii=False)
        for files in [[],[dict(extension='.txt',size=100)],[dict(extension='.pdf',size=11*1024*1024)]]:
            output=self.rt.node_main('check_file',state=state,value=files)
            self.assertEqual(output['ok'],'no')
        good=self.rt.node_main('check_file',state=state,value=[dict(extension='.pdf',size=100)])
        self.assertEqual(good['ok'],'yes')

    def test_generic_word_material_is_refined_without_duplicate_and_retains_word_metadata(self):
        context=self.rt.resolve_context(self.approval,self.business)
        word=self.rt.validate_word(self.word_decisions(context),self.bundle,context,self.business)
        word[0]['objectKinds']=[]
        word[0]['instances'][0].update(objectKey='',subject='')
        customer=next(o for o in context['objects'] if o['kind']=='customer')
        phrase=self.phrase_decisions(word_key='w1')
        phrase[0]['products'][0]['materials'][0]['instances'][0].update(objectKey=customer['objectKey'],subject=customer['name'])
        result=self.rt.finish(self.business,self.approval,self.bundle,context,word,phrase)
        self.assertEqual(result['data']['total'],1)
        item=next(self.rt.iter_materials(result['data']['items']))
        self.assertEqual(item['sourceSystem'],'上银办')
        self.assertIn(customer['name'],item['materialName'])

    def test_phrase_material_uses_highcode_word_metadata(self):
        self.business['grtstlRcmbNm']=''
        self.bundle['rules'][2]['cells'][4]='制度依据：依据甲\n发布日期：2026年10月1日\n发布部门：部门甲'
        context=self.rt.resolve_context(self.approval,self.business)
        word=self.rt.validate_word(self.word_decisions(context),self.bundle,context,self.business)
        result=self.rt.finish(self.business,self.approval,self.bundle,context,word,self.phrase_decisions())
        item=list(self.rt.iter_materials(result['data']['items']))[1]
        self.assertEqual(item['sourceSystem'],'上银办')
        self.assertEqual(item['policyBasis'],'依据甲')
        self.assertEqual(item['publishDate'],'2026年10月1日')
        self.assertEqual(item['publishDepartment'],'部门甲')

    def test_stage_exclusion_still_applies_when_phrase_requests_the_material(self):
        context=self.rt.resolve_context(self.approval,self.business)
        for reason in ['Word自身不适用','主体和事实未知','一级目录排除','批复另有否定或豁免']:
            with self.subTest(reason=reason):
                word=self.rt.validate_word(self.word_decisions(context),self.bundle,context,self.business)
                word[2].update(action='EXCLUDE',reason=reason,instances=[])
                approval=copy.deepcopy(self.approval)
                approval['text']+=' 后文明确无需签约合同。'
                result=self.rt.finish(self.business,approval,self.bundle,context,word,self.phrase_decisions())
                self.assertEqual(result['data']['total'],1)


if __name__ == '__main__':
    unittest.main()
