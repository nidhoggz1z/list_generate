"""Public A output drops productName while previous persisted results remain readable."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.material_tree import iter_materials
from gateway.callback import format_callback_materials
from gateway.result_format import build_callback
from gateway.task_models import HistoricalTaskResult, TaskDoneResponse, TaskResult
from gateway.worker import validate_outputs


def contains_product_name(value):
    if isinstance(value, dict):
        return 'productName' in value or any(contains_product_name(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_product_name(item) for item in value)
    return False


def fixture(previous=False):
    item = dict(itemId='R0001', itemType='MATERIAL', materialName='[产品甲]身份证（张甲）',
                sourceSystem='上银办', policyBasis='制度依据', publishDate='2026年',
                publishDepartment='上海银行', applicability='APPLICABLE', requirement='签约前提交')
    product = dict(extractedProductName='产品甲', checklistFileName='example.docx')
    tree = dict(categories=[dict(categoryId='1', categoryName='签约', children=[], materials=[item])])
    if previous:
        item['productName'] = product['productName'] = tree['productName'] = '产品甲'
    return dict(generatedAt='2026-11-25T00:00:00+00:00', total=1, products=[product], items=[tree])


class OutputWithoutProductNameTests(unittest.TestCase):
    def test_current_and_previous_shapes_serialize_without_product_name(self):
        for previous in (False, True):
            data = fixture(previous)
            original = copy.deepcopy(data)
            result = TaskResult.model_validate(data).model_dump(mode='json')
            self.assertFalse(contains_product_name(result))
            self.assertEqual(data, original)
            self.assertEqual(next(iter_materials(result['items']))['materialName'], '[产品甲]身份证（张甲）')

    def test_historical_result_and_public_response_remain_readable(self):
        historical = HistoricalTaskResult.model_validate(dict(fixture(True), sourceDocumentHash='a' * 64))
        response = TaskDoneResponse.model_validate(dict(bsnEvtId='B1', userNo='U1', taskId='T1',
            status='DONE', message='生成完成', data=historical)).model_dump(mode='json')
        self.assertFalse(contains_product_name(response['data']))
        self.assertEqual(response['data']['sourceDocumentHash'], 'a' * 64)

    def test_worker_accepts_new_a_and_keeps_cp_callback(self):
        identity = dict(bsnEvtId='B1', userNo='U1', taskId='T1')
        data = fixture()
        for material in iter_materials(data['items']):
            material.pop('itemId')
        callback = build_callback('T1', 'DONE', data['generatedAt'], iter_materials(data['items']))
        stored, sent = validate_outputs(identity, dict(
            result=dict(**identity, message='生成完成', data=data), callbackResult=callback))
        self.assertFalse(contains_product_name(stored))
        self.assertEqual(sent, callback)
        self.assertEqual(next(iter_materials(stored['items']))['itemId'], 'R0001')

    def test_schema_counts_ids_and_empty_directories(self):
        schema = TaskResult.model_json_schema()
        for definition in schema.get('$defs', {}).values():
            self.assertNotIn('productName', definition.get('properties', {}))
        for change in ('count', 'id', 'empty'):
            data = fixture()
            if change == 'count':
                data['total'] = 2
            elif change == 'id':
                data['items'][0]['categories'][0]['materials'][0]['itemId'] = 'R0002'
            else:
                data['items'][0]['categories'][0]['materials'] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                TaskResult.model_validate(data)

    def test_callback_uses_task_product_when_a_has_no_product_name(self):
        data = fixture()
        data['products'][0]['extractedProductName'] = '批复产品别名'
        material = next(iter_materials(data['items']))
        material['materialName'] = '[产品甲][核查]身份证[产品甲]'
        body = build_callback('T1', 'DONE', data['generatedAt'], iter_materials(data['items']))
        original = copy.deepcopy(body)
        formatted = format_callback_materials(body, data, '产品甲')
        self.assertEqual(formatted['checkListItems'][0]['materialName'], '[核查]身份证[产品甲]')
        self.assertEqual(body, original)


if __name__ == '__main__':
    unittest.main()
