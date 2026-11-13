"""Flat terminal callback with a latest-submission check before sending."""
import asyncio
from datetime import datetime
import logging
import re
from uuid import uuid4

import httpx

from .service_log import BEIJING,event
from .material_tree import iter_materials
from .result_format import build_callback
from .config import DEFAULT_CALLBACK_URL

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


def format_callback_materials(body, result):
    materials = iter_materials((result or {}).get('items', []))
    items = []
    for item in body['checkListItems']:
        product = next(materials, {}).get('productName')
        formatted = dict(item)
        if product:
            formatted['materialName'] = item['materialName'].removeprefix(f'[{product}]')
        formatted['publishDate'] = re.sub(r'(?<=\d)[ \t]*T[ \t]*(?=\d)', ' ', item['publishDate'], count=1)
        for field in ('sourceSystem', 'publishDepartment'):
            formatted[field] = re.sub(r'[。.]+(?=\s*$)', '', item[field])
        items.append(formatted)
    return dict(body, checkListItems=items)


class TaskCallback:
    def __init__(self, store, *, callback_url=DEFAULT_CALLBACK_URL, transport=None):
        self.store = store
        self.callback_url = callback_url
        self.http = httpx.AsyncClient(timeout=10,trust_env=False,transport=transport)
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
                body = format_callback_materials(body, record.get('result'))
            headers = {'c-version':'4.0','g-svc-cd':'152517942','g-cnsm-side-sys-ind':'HAW',
                       'g-cnsm-side-srl-id':'C'+uuid4().hex,'g-fullchnl-srl-id':'F'+uuid4().hex,
                       'g-cnsm-side-trans-tm':bank_time(),'g-chnl-id':'HAW','g-chnl-trans-cd':'01',
                       'Content-Type':'application/json'}
            if check_latest and not self.store.is_latest(record['generationId']):
                event('发送前发现更新任务，旧回调已跳过',record)
                return
            # Single attempt, ignore HTTP status/body, do not follow redirects.
            async with self.http.stream('POST',self.callback_url,headers=headers,json=body):
                pass
            event('任务终态推送已发送',record,status=record['status'])
        except Exception as error:
            event('任务终态推送异常',record,level=logging.WARNING,error_type=type(error).__name__)
