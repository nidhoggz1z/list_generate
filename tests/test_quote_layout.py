"""PDF whitespace differences may change layout, never the quoted content."""
import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'nodes'))
import runtime as rt


class QuoteLayoutTests(unittest.TestCase):
    def approval(self,source):
        return dict(products=[dict(productName='固定资产贷款',extractedProductName='固定资产贷款',source='固定资产贷款')],facts=[
            dict(name='备用非承敞口金额',value='0.00',productName='固定资产贷款',subject='',present=True,negated=False,source=source)])

    def test_fact_quote_restored_to_exact_original_span_without_changing_document(self):
        original='固定资产贷款。备用非承敞口金\r\n额\r\n0.00。'
        reply=self.approval('备用非承敞口金\r\n额\r\n\r\n0.00')
        result=rt.validate_approval(reply,original,['固定资产贷款'])
        self.assertEqual(result['facts'][0]['source'],'备用非承敞口金\r\n额\r\n0.00')
        self.assertEqual(result['text'],original)
        self.assertEqual(reply['facts'][0]['source'],'备用非承敞口金\r\n额\r\n\r\n0.00')

    def test_wrapped_product_name_keeps_product_identity_and_restores_quote(self):
        original='可售产品：固定资\r\n产贷款。'
        reply=dict(products=[dict(productName='固定资产贷款',extractedProductName='固定资产贷款',source='固定资产贷款')],facts=[])
        result=rt.validate_approval(reply,original,['固定资产贷款'])
        self.assertEqual(result['products'][0]['productName'],'固定资产贷款')
        self.assertEqual(result['products'][0]['source'],'固定资\r\n产贷款')

    def test_content_changes_remain_rejected(self):
        original='固定资产贷款。无需提供抵押；金额6000.00万元；客户甲。'
        for source in ['需要提供抵押','金额6001.00万元','客户乙','金额6000,00万元','无需提供抵押金额6000.00万元']:
            with self.subTest(source=source),self.assertRaises(ValueError):
                rt.validate_approval(self.approval(source),original,['固定资产贷款'])

    def test_whitespace_does_not_join_separate_ascii_words_or_numbers(self):
        for original,source in [('固定资产贷款。金额60 00万元。','金额6000万元'),
                                ('固定资产贷款。标识ABC\nDEF。','标识ABCDEF')]:
            with self.subTest(original=original),self.assertRaises(ValueError):
                rt.validate_approval(self.approval(source),original,['固定资产贷款'])

    def test_exact_quote_stays_unchanged_and_blank_quote_fails(self):
        original='固定资产贷款。金额0.00。'
        result=rt.validate_approval(self.approval('金额0.00'),original,['固定资产贷款'])
        self.assertEqual(result['facts'][0]['source'],'金额0.00')
        with self.assertRaises(ValueError):
            rt.validate_approval(self.approval(' \t\n'),original,['固定资产贷款'])

    def test_layout_spaces_and_tabs_are_restored_with_source_offsets(self):
        original='固定资产贷款。以甲所有的\t在建工\r\n程及相应土地使用权。'
        result=rt.validate_approval(self.approval('以甲所有的在建工程及相应土地使用权'),original,['固定资产贷款'])
        self.assertEqual(result['facts'][0]['source'],'以甲所有的\t在建工\r\n程及相应土地使用权')


if __name__=='__main__':
    unittest.main()
