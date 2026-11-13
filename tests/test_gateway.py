"""Offline boundary tests: no PDF download, model requests or bank callbacks."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def business(number='1', task='T1'):
    return dict(bsnEvtId='B'+number, userNo='U1', taskId=task,
                replyImageUrl='/NCPC/NCPCFILE/20260729/test/file.pdf', prcsStage='放款', aspdNm='产品甲')


def workflow_result(data):
    leaf = dict(itemType='MATERIAL', productName='产品甲', materialName='[产品甲]营业执照',
                sourceSystem='上银办', policyBasis='', publishDate='2026年',
                publishDepartment='上海银行', applicability='APPLICABLE', requirement='')
    tree = dict(productName='产品甲', categories=[dict(categoryId='1', categoryName='基本资料',
                children=[dict(categoryId='1.1', categoryName='主体资料', children=[],
                               materials=[copy.deepcopy(leaf)])], materials=[leaf])])
    return dict(**{k: data[k] for k in ('bsnEvtId','userNo','taskId')}, message='生成完成',
            data=dict(generatedAt='2026-11-02T00:00:00+00:00', total=2,
                      products=[dict(extractedProductName='产品甲',productName='产品甲',
                                     checklistFileName='产品甲材料清单.docx')], items=[tree]))


def workflow_outputs(data):
    from gateway.material_tree import iter_materials
    from gateway.result_format import build_callback
    result = workflow_result(data)
    flat = build_callback(data['taskId'], 'DONE', result['data']['generatedAt'],
                          iter_materials(result['data']['items']))
    return dict(result=result, callbackResult=flat)


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('gateway.main'), 'gateway not implemented')
        from gateway.store import TaskStore
        self.directory = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.directory.name))
        self.addCleanup(self.directory.cleanup)

    async def spin(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.001)

    async def test_five_active_sixth_waits_and_queue_survives_reopen(self):
        from gateway.worker import TaskWorker
        gates, started, sent = {}, [], []
        async def run(record):
            key = record['generationId']
            started.append(record['taskId'])
            await gates[key].wait()
            return workflow_outputs(record['businessData'])
        for n in range(6):
            row = self.store.accept(business(str(n), 'T'+str(n)))
            gates[row['generationId']] = asyncio.Event()
        # The queue is durable; the new object reads the same SQLite database.
        from gateway.store import TaskStore
        self.store = TaskStore(Path(self.directory.name))
        worker = TaskWorker(self.store, run, sent.append)
        worker.start()
        try:
            await self.spin(lambda: len(started)==5)
            self.assertEqual(started, [f'T{n}' for n in range(5)])
            self.assertEqual(self.store.counts(), {'PENDING':1,'PROCESSING':5})
            next(iter(gates.values())).set()
            await self.spin(lambda: len(started)==6)
            for gate in gates.values():
                gate.set()
            await self.spin(lambda: len(sent)==6)
            self.assertEqual([m['itemId'] for m in sent[0]['result']['items'][0]['categories'][0]['children'][0]['materials']], ['R0001'])
            self.assertEqual(sent[0]['result']['items'][0]['categories'][0]['materials'][0]['itemId'], 'R0002')
        finally:
            await worker.stop()

    async def test_old_run_continues_but_only_latest_failure_is_sent(self):
        from gateway.worker import TaskWorker
        from gateway.client import WorkflowError
        old_gate, new_gate = asyncio.Event(), asyncio.Event()
        started, finished, sent = [], [], []
        async def run(record):
            task = record['taskId']
            started.append(task)
            await (old_gate if task=='OLD' else new_gate).wait()
            finished.append(task)
            if task=='NEW':
                raise WorkflowError('最新批复下载失败')
            return workflow_outputs(record['businessData'])
        self.store.accept(business(task='OLD'))
        worker = TaskWorker(self.store, run, sent.append)
        worker.start()
        try:
            await self.spin(lambda: started==['OLD'])
            self.store.accept(business(task='NEW'))
            worker.notify()
            await self.spin(lambda: len(started)==2)
            new_gate.set()
            await self.spin(lambda: len(sent)==1)
            old_gate.set()
            await self.spin(lambda: len(finished)==2)
            await asyncio.sleep(0.01)
            self.assertEqual([r['taskId'] for r in sent], ['NEW'])
            self.assertEqual(sent[0]['status'], 'FAILED')
        finally:
            await worker.stop()

    async def test_same_number_resubmission_and_restart_keep_latest_generation(self):
        first = self.store.accept(business())
        second = self.store.accept(business())
        self.assertNotEqual(first['generationId'], second['generationId'])
        self.assertFalse(self.store.is_latest(first['generationId']))
        self.assertTrue(self.store.is_latest(second['generationId']))
        recovered = self.store.recover_interrupted()
        self.assertEqual(len(recovered), 2)
        self.assertEqual([r['generationId'] for r in recovered if self.store.is_latest(r['generationId'])],
                         [second['generationId']])
        self.assertEqual(self.store.recover_interrupted(), [])

    async def test_callback_rechecks_latest_before_network_and_keeps_bank_headers(self):
        from gateway.callback import TaskCallback
        requests = []
        async def receive(request):
            requests.append(request)
            return httpx.Response(500)
        row = self.store.accept(business())
        row.update(status='FAILED', reason='下载失败')
        async with TaskCallback(self.store, transport=httpx.MockTransport(receive)) as callback:
            callback.notify(row)
            latest = self.store.accept(business(task='NEW'))
            latest.update(status='FAILED', reason='新任务失败')
            callback.notify(latest)
        self.assertEqual(len(requests), 1)
        body = json.loads(requests[0].content)
        self.assertEqual(body['taskId'], 'NEW')
        self.assertEqual(body['taskStatus'], 'FAILED')
        self.assertEqual(body['errorMsg'], '新任务失败')
        self.assertEqual(body['checkListItems'], [])
        self.assertEqual(requests[0].headers['g-svc-cd'], '152517942')
        self.assertNotEqual(requests[0].headers['g-cnsm-side-srl-id'], requests[0].headers['g-fullchnl-srl-id'])
        self.assertNotIn('data', body)

    async def test_success_callback_formats_only_the_outgoing_materials(self):
        from gateway.callback import TaskCallback
        from gateway.material_tree import iter_materials
        from gateway.result_format import build_callback
        from gateway.worker import validate_outputs

        record = self.store.accept(business())
        self.store.claim()
        result = workflow_result(record)
        first, second = iter_materials(result['data']['items'])
        first.update(materialName='[产品甲]一级目录-营业执照（原件）', publishDate='2026-09-29',
                     sourceSystem='系统.A。', publishDepartment='发布。部门.')
        second.update(materialName='[产品甲][核查]名称[产品甲]', publishDate='2026-09-30 11:22:33',
                      sourceSystem='无句号', publishDepartment='部门。 ')
        callback_body = build_callback(record['taskId'], 'DONE', result['data']['generatedAt'],
                                       iter_materials(result['data']['items']))
        stored, checked = validate_outputs(record, dict(result=result, callbackResult=callback_body))
        saved = self.store.finish(record['generationId'], result=stored)
        original = copy.deepcopy(checked)
        requests = []

        def receive(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200)

        async with TaskCallback(self.store, transport=httpx.MockTransport(receive)) as callback:
            await callback.send(dict(saved, callbackResult=checked))
        expected = copy.deepcopy(original)
        expected['checkListItems'][0].update(
            materialName='一级目录-营业执照（原件）', publishDate='2026-09-29 00:00:00',
            sourceSystem='系统.A', publishDepartment='发布。部门')
        expected['checkListItems'][1].update(materialName='[核查]名称[产品甲]',
                                             publishDate='2026-09-30 11:22:33', publishDepartment='部门 ')
        self.assertEqual(requests, [expected])
        self.assertEqual(checked, original)
        self.assertEqual(self.store.get(record['generationId'])['result'], stored)

    async def test_create_accepts_without_pdf_network_and_invalid_input_does_not_replace(self):
        from gateway.main import create_app
        from gateway.config import Settings
        started, gate = [], asyncio.Event()
        callbacks = []
        callback_url = "https://cp.example.test/v2/callback"

        def receive(request):
            callbacks.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200)
        async def run(record):
            started.append(record)
            await gate.wait()
            return workflow_outputs(record['businessData'])
        app = create_app(Settings(storage_root=Path(self.directory.name), api_key='test',
                                  api_url='http://unused/v1', callback_url=callback_url), run_workflow=run,
                         callback_transport=httpx.MockTransport(receive))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://gateway') as client:
                response = await client.post('/api/v1/tasks', json=business())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'bsnEvtId': 'B1', 'userNo': 'U1',
                                                   'taskId': 'T1', 'autoReply': 'task created'})
                await self.spin(lambda:len(started)==1)
                generation = started[0]['generationId']
                bad = await client.post('/api/v1/tasks', json=business(task='BAD')|{'taskId':''})
                self.assertEqual(bad.status_code, 422)
                incomplete = business(task='INCOMPLETE')
                del incomplete['aspdNm']
                incomplete.update(userNo=None, prcsStage='  ', replyImageUrl='')
                try:
                    received = await client.post('/api/v1/tasks', json=incomplete)
                    self.assertEqual(received.status_code, 200)
                    message = '参数 userNo、aspdNm、prcsStage、replyImageUrl 缺失，无法创建任务。'
                    self.assertEqual(received.json(), {'bsnEvtId': 'B1', 'userNo': None,
                                                       'taskId': 'INCOMPLETE', 'autoReply': message})
                    await self.spin(lambda: len(callbacks) == 1)
                    url, body = callbacks[0]
                    self.assertEqual(url, callback_url)
                    self.assertEqual(set(body), {'taskId', 'taskStatus', 'generatedAt', 'errorMsg', 'checkListItems'})
                    self.assertEqual(body['taskId'], 'INCOMPLETE')
                    self.assertEqual(body['taskStatus'], 'FAILED')
                    self.assertEqual(body['errorMsg'], received.json()['autoReply'])
                    self.assertEqual(body['checkListItems'], [])
                    self.assertEqual(app.state.store.counts(), {'PROCESSING': 1})
                    self.assertEqual(len(started), 1)
                    invalid = await client.post('/api/v1/tasks', json=business(task='INVALID') | {'aspdNm': 123})
                    self.assertEqual(invalid.status_code, 422)
                except BaseException:
                    gate.set()
                    raise
                wrong_type=await client.post('/api/v1/tasks',content=json.dumps(business()),headers={'Content-Type':'text/plain'})
                self.assertEqual(wrong_type.status_code,400)
                self.assertTrue(app.state.store.is_latest(generation))
                self.assertEqual((await client.post('/api/v1/tasks/query', json={})).status_code,404)
                files = (await client.get('/v1/checklist-files')).json()['files']
                filename = files[0]['fileName']
                document = await client.post('/v1/checklist-files/content', json={'fileName': filename})
                self.assertEqual(document.status_code, 200)
                self.assertEqual(document.json()['fileName'], filename)
                self.assertTrue(document.json()['content'])
                for value, code in (('../' + filename, 422), ('不存在.docx', 404)):
                    response = await client.post('/v1/checklist-files/content', json={'fileName': value})
                    self.assertEqual(response.status_code, code)
                gate.set()
                await self.spin(lambda: len(callbacks) == 2)
                self.assertEqual([(url, body['taskId'], body['taskStatus']) for url, body in callbacks],
                                 [(callback_url, 'INCOMPLETE', 'FAILED'), (callback_url, 'T1', 'DONE')])


class DifyClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_runs_use_configured_endpoint_and_keep_inputs_separate(self):
        from gateway.client import DifyClient
        from gateway.config import Settings
        business_inputs = {
            task: dict(business(task=task), subcontracts=[{'gnrNm': '示例担保人'}])
            for task in ('A', 'B')
        }
        business_inputs['B']['replyImageUrl'] = 'https://example.test/approval.pdf?token=sample'
        received = {}
        run_ids = {}
        url = 'http://hiagent.test/api/proxy/api/v1/sync_run_app_workflow'
        async def receive(request):
            self.assertEqual(request.method, 'POST')
            self.assertEqual(str(request.url), url)
            self.assertEqual(request.headers['Apikey'], 'test')
            self.assertNotIn('Authorization', request.headers)
            body = json.loads(request.content)
            self.assertEqual(set(body), {'InputData', 'UserID'})
            self.assertEqual(body['UserID'], 'CP')
            self.assertIsInstance(body['InputData'], str)
            start_input = json.loads(body['InputData'])
            self.assertIsInstance(start_input['businessData'], str)
            data = json.loads(start_input['businessData'])
            if data['taskId'] == 'A':
                self.assertEqual(set(start_input), {'businessData', 'image_platform_download_base_url', 'image_platform_au'})
                self.assertEqual(start_input['image_platform_download_base_url'], 'http://images.test/download')
                self.assertEqual(start_input['image_platform_au'], 'synthetic-au')
            else:
                self.assertEqual(set(start_input), {'businessData'})
            received[data['taskId']] = data
            return httpx.Response(200, json=dict(runId='RUN-'+data['taskId'], status='success',
                output=json.dumps(workflow_outputs(data), ensure_ascii=False)))
        async with DifyClient(Settings(api_url=url, api_key='test',
                                      image_platform_download_base_url='http://images.test/download',
                                      image_platform_au='synthetic-au'),
                              transport=httpx.MockTransport(receive), on_started=run_ids.__setitem__) as client:
            results = await asyncio.gather(*(client.run(dict(generationId=task, businessData=data))
                                             for task, data in business_inputs.items()))
        self.assertEqual([r['result']['taskId'] for r in results], ['A','B'])
        self.assertEqual(received, business_inputs)
        self.assertEqual(run_ids, {'A': 'RUN-A', 'B': 'RUN-B'})

    async def test_sync_output_is_accepted_and_preserved_in_callback(self):
        from gateway.callback import terminal_response
        from gateway.client import DifyClient
        from gateway.config import Settings
        from gateway.worker import validate_outputs
        from gateway.client import WorkflowError
        request = business()
        output = workflow_outputs(request)
        payload = dict(output=json.dumps(output))
        async with DifyClient(Settings(api_url='http://hiagent/sync_run_app_workflow',api_key='test'),
            transport=httpx.MockTransport(lambda r:httpx.Response(200, json=payload))) as client:
            returned = await client.run(dict(businessData=request))
        stored, flat = validate_outputs(request, returned)
        self.assertEqual(terminal_response(dict(request,status='DONE',result=stored,callbackResult=flat)), flat)
        returned['callbackResult']['checkListItems'][0]['materialName'] = '不一致'
        with self.assertRaises(WorkflowError):
            validate_outputs(request, returned)

    async def test_valid_business_failure_keeps_callback(self):
        from gateway.result_format import build_callback
        from gateway.worker import validate_outputs
        request = business()
        reason = '未生成适用材料'
        result = {key: request[key] for key in ('bsnEvtId','userNo','taskId')}
        result.update(status='FAILED', reason=reason)
        flat = build_callback(request['taskId'], 'FAILED', '2026-11-06T08:05:30', error_msg=reason)
        self.assertEqual(validate_outputs(request, dict(result=result, callbackResult=flat)), (None, flat))

    async def test_sync_transport_and_invalid_results_are_failures(self):
        from gateway.client import DifyClient, WorkflowError
        from gateway.config import Settings
        cases = [
            (404, {'message': 'sensitive upstream detail'}, 'HTTP 404'),
            (200, {'status': 'failed', 'message': 'sensitive upstream detail'}, 'failed'),
            (200, {'status': 'processing'}, 'processing'),
            (200, {'runId': 'RUN-empty', 'nodes': {}, 'steps': [], 'output': 'null'}, 'output 为 null'),
            (200, {'status': 'success', 'output': 'sensitive non-JSON output'}, 'JSON'),
            (200, {'status': 'success', 'output': '{"result":{}}'}, '完整业务结果'),
        ]
        for status, payload, expected in cases:
            with self.subTest(expected=expected):
                async with DifyClient(Settings(api_url='http://hiagent/sync_run_app_workflow',api_key='test'),
                    transport=httpx.MockTransport(lambda r:httpx.Response(status,json=payload))) as client:
                    with self.assertRaises(WorkflowError) as caught:
                        await client.run(dict(businessData=business()))
                    self.assertNotIn('sensitive',str(caught.exception))
                    self.assertIn(expected, str(caught.exception))


if __name__=='__main__':
    unittest.main()
