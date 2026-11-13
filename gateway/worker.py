"""FIFO consumers. Older generations keep running but cannot publish."""
import asyncio
from copy import deepcopy
from datetime import datetime
import logging
import sqlite3
from time import perf_counter

from .client import WorkflowError
from .material_tree import iter_materials
from .result_format import build_callback
from .service_log import BEIJING, event
from .task_models import CallbackResult, TaskResult


def adapt_result(record, output):
    if not isinstance(output,dict) or any(output.get(k)!=record[k] for k in ('bsnEvtId','userNo','taskId')):
        raise WorkflowError('工作流结果任务标识不一致')
    data = deepcopy(output['data'])
    if output.get('status') == 'DONE':
        data = CallbackResult.model_validate(data).model_dump(mode='json')
        data['generatedAt'] = datetime.strptime(data['generatedAt'], '%Y%m%d %H%M%S%f').replace(tzinfo=BEIJING).isoformat()
    else:
        for index, material in enumerate(iter_materials(data['items']),1):
            material['itemId'] = f'R{index:04d}'
    return TaskResult.model_validate(data).model_dump(mode='json')


def validate_outputs(record, outputs):
    try:
        result, callback = outputs['result'], outputs['callbackResult']
        if any(result.get(key) != record[key] for key in ('bsnEvtId', 'userNo', 'taskId')):
            raise WorkflowError('工作流结果任务标识不一致')
        if result.get('status') == 'FAILED':
            if set(result) != {'bsnEvtId', 'userNo', 'taskId', 'status', 'reason'}:
                raise ValueError()
            reason = result['reason']
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 120:
                raise ValueError()
            expected = build_callback(record['taskId'], 'FAILED', callback['generatedAt'], error_msg=reason)
            stored = None
        else:
            if set(result) != {'bsnEvtId', 'userNo', 'taskId', 'message', 'data'} or result['message'] != '生成完成':
                raise ValueError()
            stored = adapt_result(record, result)
            expected = build_callback(record['taskId'], 'DONE', stored['generatedAt'],
                                      iter_materials(stored['items']))
        if callback != expected:
            raise ValueError()
        return stored, callback
    except (ValueError, TypeError, KeyError, AttributeError):
        raise WorkflowError('工作流双格式结果无效或不一致') from None


class TaskWorker:
    concurrency = 5

    def __init__(self, store, run_workflow, notify_terminal):
        self.store,self.run_workflow,self.notify_terminal = store,run_workflow,notify_terminal
        self.wake = asyncio.Event()
        self.tasks = []

    def start(self):
        if not self.tasks:
            self.tasks = [
                asyncio.create_task(self.consume(), name=f'dify-worker-{n}')
                for n in range(1, self.concurrency + 1)
            ]

    def notify(self):
        self.wake.set()

    async def stop(self):
        # This only closes local requests on service shutdown, never on replacement.
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tasks = []

    async def consume(self):
        while True:
            self.wake.clear()
            try:
                record = self.store.claim()
                if record is None:
                    await self.wake.wait()
                    continue
                started = perf_counter()
                event('任务开始处理',record,status='PROCESSING')
                try:
                    output = await self.run_workflow(record)
                    result, callback = validate_outputs(record, output)
                    terminal = self.store.finish(record['generationId'], result=result,
                                                 reason=callback['errorMsg'] or None)
                    terminal['callbackResult'] = callback
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    reason = str(error) if isinstance(error,WorkflowError) else '任务处理失败：工作流结果或存储异常'
                    terminal = self.store.finish(record['generationId'],reason=reason)
                event('任务处理结束',record,status=terminal['status'],
                      duration_ms=round((perf_counter()-started)*1000,2))
                if self.store.is_latest(record['generationId']):
                    self.notify_terminal(terminal)
                else:
                    event('旧任务结果不推送',record,status=terminal['status'])
            except (OSError,sqlite3.Error):
                event('任务存储暂不可用',level=logging.ERROR)
                await asyncio.sleep(1)
