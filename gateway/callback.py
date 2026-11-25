"""Flat terminal callback with a latest-submission check before sending."""
import asyncio
from datetime import datetime
import json
import logging
import re
from time import perf_counter
from uuid import uuid4

import httpx

from .service_log import BEIJING,event
from .material_tree import iter_materials
from .result_format import build_callback
from .config import DEFAULT_CALLBACK_URL, endpoint_urls

CALLBACK_ATTEMPT_SECONDS = 10
CP_TASK_STATUS_CODES = {'DONE': 1, 'FAILED': 2}

def bank_time(value=None):
    value = datetime.now(BEIJING) if value is None else datetime.fromisoformat(value).astimezone(BEIJING)
    return value.strftime('%Y%m%d %H%M%S%f')[:-4]


def terminal_response(record):
    if 'callbackResult' in record:
        return record['callbackResult']
    if record['status']=='FAILED':
        return build_callback(record['taskId'], 'FAILED', datetime.now(BEIJING), error_msg=record['reason'])
    if record['status']=='DONE':
        return build_callback(record['taskId'], 'DONE', record['result']['generatedAt'],
                              iter_materials(record['result']['items']))
    raise ValueError('只推送成功或失败')


def format_callback_materials(body, result, target_product=''):
    materials = iter_materials((result or {}).get('items', []))
    products = (result or {}).get('products', [])
    if not target_product and len(products) == 1:
        target_product = products[0].get('extractedProductName', '')
    items = []
    for item in body['checkListItems']:
        product = next(materials, {}).get('productName') or target_product
        formatted = dict(item)
        if product:
            formatted['materialName'] = item['materialName'].removeprefix(f'[{product}]')
        formatted['publishDate'] = item['publishDate'][:10]
        for field in ('sourceSystem', 'publishDepartment'):
            formatted[field] = re.sub(r'[。.]+(?=\s*$)', '', item[field])
        items.append(formatted)
    return dict(body, checkListItems=items)


class TaskCallback:
    def __init__(self, store, *, callback_url=DEFAULT_CALLBACK_URL, transport=None):
        self.store = store
        self.urls = endpoint_urls(callback_url, 'CALLBACK_URL', allow_query=True) or (DEFAULT_CALLBACK_URL,)
        self.http = httpx.AsyncClient(timeout=CALLBACK_ATTEMPT_SECONDS, trust_env=False, transport=transport)
        self.pending = set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        try:
            await self.drain()
        finally:
            await self.http.aclose()

    def notify_rejected(self, task_id, reason):
        self.notify(dict(taskId=task_id, status='FAILED', reason=reason), check_latest=False)

    def notify(self, record, *, check_latest=True):
        task = asyncio.create_task(self.send(dict(record), check_latest=check_latest), name='task-callback')
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def drain(self):
        while self.pending:
            await asyncio.gather(*self.pending)

    async def send(self, record, *, check_latest=True):
        try:
            body = terminal_response(record)
            if body['taskStatus'] == 'DONE':
                body = format_callback_materials(body, record.get('result'),
                                                 record.get('businessData', {}).get('aspdNm', ''))
            headers = {'c-version':'4.0','g-svc-cd':'152517942','g-cnsm-side-sys-ind':'HAW',
                       'g-cnsm-side-srl-id':'C'+uuid4().hex,'g-fullchnl-srl-id':'F'+uuid4().hex,
                       'g-cnsm-side-trans-tm':bank_time(),'g-chnl-id':'HAW','g-chnl-trans-cd':'01',
                       'Content-Type':'application/json'}
            await self.send_to_endpoints(record, check_latest, body, headers)
        except Exception as error:
            event('任务终态推送异常',record,level=logging.WARNING,error_type=type(error).__name__)

    async def send_to_endpoints(self, record, check_latest, body, headers):
        items = body['checkListItems']
        payload = {
            'taskId': body['taskId'],
            'taskStCd': CP_TASK_STATUS_CODES[body['taskStatus']],
            'generatedAt': body['generatedAt'].replace(' ', 'T', 1),
            'errorMsg': body['errorMsg'],
            'paramsJson': json.dumps(items, ensure_ascii=False, separators=(',', ':')),
        }
        loop = asyncio.get_running_loop()
        deadline = record.get('taskDeadline', loop.time() + CALLBACK_ATTEMPT_SECONDS * len(self.urls))
        for attempt, url in enumerate(self.urls, 1):
            if check_latest and not self.store.is_latest(record['generationId']):
                event('发送前发现更新任务，旧回调已跳过', record, attempt=attempt)
                return
            if loop.time() >= deadline:
                event('任务时间预算已用尽，回调结束', record, level=logging.WARNING, attempts=attempt - 1)
                return
            started = perf_counter()
            status_code = None
            event('任务终态开始推送', record, attempt=attempt, endpoint_count=len(self.urls),
                  status=body['taskStatus'], material_count=len(items))
            try:
                async with asyncio.timeout_at(min(deadline, loop.time() + CALLBACK_ATTEMPT_SECONDS)):
                    async with self.http.stream('POST', url, headers=headers, json=payload) as response:
                        status_code = response.status_code
                        if not response.is_stream_consumed:
                            async for _ in response.aiter_raw():
                                pass
                event('任务终态推送已发送' if response.is_success else 'CP 回调返回非成功 HTTP 状态', record,
                      level=logging.INFO if response.is_success else logging.WARNING,
                      status=body['taskStatus'], http_status=status_code, attempt=attempt,
                      duration_ms=round((perf_counter() - started) * 1000, 2))
                return
            except (httpx.TransportError, TimeoutError) as error:
                event('任务终态推送尝试失败', record, level=logging.WARNING, attempt=attempt,
                      http_status=status_code, error_type=type(error).__name__,
                      retry=attempt < len(self.urls) and loop.time() < deadline,
                      duration_ms=round((perf_counter() - started) * 1000, 2))
        event('任务终态推送失败，已尝试所有回调地址', record, level=logging.WARNING, attempts=len(self.urls))
