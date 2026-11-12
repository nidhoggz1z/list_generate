"""Changed business decisions agreed for the high-code wrapper integration."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'nodes'),str(ROOT/'tests')]
import runtime as rt
import test_contract as contracts
from test_contract import fixture


def phrase(bundle, *, word_key='w3', applicability='APPLICABLE'):
    return [dict(rowKey='row:2',scope='EXPLICIT',matches=[dict(page_number=1,quote='须提供签约合同')],
                 products=[dict(productName='产品甲',materials=[dict(materialKey='row:2/item:1',
                    applicability=applicability,categoryPath=['2'],instances=[dict(objectKey='',subject='',
                        materialName='签约合同',requirement='提供合同',wordRuleKey=word_key)]
                    if applicability=='APPLICABLE' else [])])])]


class IntegrationRulesTests(unittest.TestCase):
    def test_skip_unmatched_product_and_filter_only_its_scoped_facts(self):
        b,a,_ = fixture()
        a['products'].append(dict(extractedProductName='未知产品',productName='未知产品',source='未知产品'))
        a['facts'] = [dict(productName=n,name='事实',value='值') for n in ('','产品甲','未知产品')]
        state = dict(businessData=b,approval=a,catalog=['产品甲'])
        output = rt.node_main('validate_matches',state=json.dumps(state,ensure_ascii=False),
            value=dict(matches=[dict(extractedProductName='未知产品',checklistProductName='')]))
        self.assertEqual(output['ok'],'yes')
        saved = json.loads(output['state'])
        self.assertEqual([p['productName'] for p in saved['approval']['products']],['产品甲'])
        self.assertEqual([f['productName'] for f in saved['approval']['facts']],['','产品甲'])
        self.assertEqual(len(saved['originalApproval']['products']),2)
        self.assertEqual(json.loads(output['request']),dict(products=['产品甲']))
        self.assertEqual(saved['productMatches'][1]['checklistProductName'],'')

    def test_all_unmatched_returns_failed_not_empty_rule_request(self):
        b,a,_ = fixture()
        output = rt.node_main('validate_matches',state=json.dumps(dict(businessData=b,approval=a,catalog=['其他产品']),ensure_ascii=False),
            value=dict(matches=[dict(extractedProductName='产品甲',checklistProductName='')]))
        self.assertEqual(output['ok'],'no')
        self.assertEqual(output['result']['status'],'FAILED')
        self.assertIn('所有产品均未匹配',output['result']['reason'])

    def test_phrase_cannot_restore_stage_filtered_directory(self):
        b,a,bundle = fixture()
        fixture_test = contracts.ContractTests()
        fixture_test.bundle = bundle
        context = rt.resolve_context(a,b)
        word = rt.validate_word(fixture_test.word_decisions(context),bundle,context,b)
        result = rt.finish(b,a,bundle,context,word,phrase(bundle))
        self.assertEqual(result['data']['total'],1)
        self.assertTrue(all('itemId' not in m for m in rt.iter_materials(result['data']['items'])))

    def test_unknown_phrase_material_does_not_get_added(self):
        b,a,bundle = fixture()
        fixture_test = contracts.ContractTests()
        fixture_test.bundle = bundle
        context = rt.resolve_context(a,b)
        word = rt.validate_word(fixture_test.word_decisions(context),bundle,context,b)
        result = rt.finish(b,a,bundle,context,word,phrase(bundle,applicability='UNKNOWN'))
        self.assertEqual(result['data']['total'],1)

    def test_phrase_request_declares_all_requested_rows_and_whole_document_page(self):
        b,a,bundle=fixture()
        fixture_test=contracts.ContractTests()
        fixture_test.bundle=bundle
        context=rt.resolve_context(a,b)
        word=fixture_test.word_decisions(context)
        output=rt.node_main('validate_word',state=json.dumps(dict(businessData=b,approval=a,bundle=bundle,
            context=context),ensure_ascii=False),value=dict(decisions=word))
        self.assertEqual(output['ok'],'yes')
        request=json.loads(output['request'])
        self.assertEqual(request['requestedRowKeys'],['row:2'])
        self.assertEqual(request['approval']['pages'][0]['text'],a['text'])

    def test_download_path_is_resolved_inside_dify_without_changing_business_json(self):
        b,_,_ = fixture()
        path = '/NCPC/NCPCFILE/日期/批次/文件.pdf'
        b['replyImageUrl'] = path
        output = rt.node_main('input',businessData=json.dumps(b,ensure_ascii=False),
            download_base_url='https://image/internal/File',image_au='a+b&c')
        self.assertEqual(output['ok'],'yes')
        from urllib.parse import urlsplit,parse_qs,unquote
        self.assertEqual(parse_qs(urlsplit(output['url']).query),{'au':['a+b&c']})
        self.assertTrue(unquote(output['url']).startswith('https://image/internal/File'+path))
        self.assertEqual(json.loads(output['state'])['businessData']['replyImageUrl'],path)
        missing = rt.node_main('input',businessData=json.dumps(b,ensure_ascii=False))
        self.assertEqual(missing['ok'],'no')
        self.assertEqual(missing['result']['taskId'],'T1')


if __name__=='__main__':
    unittest.main()
