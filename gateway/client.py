"""Call HiAgent's synchronous workflow API using asynchronous HTTP I/O."""
import asyncio
import json
import logging
from time import perf_counter
import httpx

from .images import ImageDownloadError, TaskImages
from .service_log import event


class WorkflowError(Exception):
    """A safe error message; raw HTTP/provider responses never leave the client."""


def network_causes(error):
    causes = []
    for _ in range(8):
        if error is None:
            break
        causes.append(dict(error_type=type(error).__name__, errno=getattr(error, 'errno', None)))
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return causes


class DifyClient:
    def __init__(self, settings, *, transport=None, on_started=None):
        self.settings = settings
        self.images = TaskImages(settings)
        self.on_started = on_started
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(settings.read_timeout,connect=10),
                                     trust_env=False,transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def run(self, record):
        try:
            async with self.images.prepare(self.http, record) as source:
                return await self._run(record, source)
        except ImageDownloadError as error:
            raise WorkflowError(str(error)) from None

    async def _run(self, record, source):
        started = perf_counter()
        phase = 'prepare'
        status_code = None
        try:
            business = record['businessData']
            if source != business.get('replyImageUrl', ''):
                business = dict(business, replyImageUrl=source)
            start_input = {'businessData': json.dumps(business, ensure_ascii=False, allow_nan=False)}
            if not business.get('replyImageUrl', '').lower().startswith(('http://', 'https://')):
                if self.settings.image_platform_download_base_url:
                    start_input['image_platform_download_base_url'] = self.settings.image_platform_download_base_url
                if self.settings.image_platform_au:
                    start_input['image_platform_au'] = self.settings.image_platform_au
            body = {
                'InputData': json.dumps(start_input, ensure_ascii=False),
                'UserID': 'CP',
            }
            phase = 'response_headers'
            event('HiAgent 工作流开始请求', record, read_timeout_s=self.settings.read_timeout, connect_timeout_s=10)
            async with self.http.stream('POST', self.settings.api_url,
                                        headers={'Apikey': self.settings.api_key}, json=body) as response:
                status_code = response.status_code
                phase = 'response_body'
                event('HiAgent 响应头已收到', record, status_code=status_code,
                      elapsed_ms=round((perf_counter() - started) * 1000, 2))
                await response.aread()
            phase = 'parse_response'
            event('HiAgent 响应体已读完', record, status_code=status_code, response_bytes=len(response.content),
                  elapsed_ms=round((perf_counter() - started) * 1000, 2))
            if response.status_code != 200:
                raise WorkflowError(f'HiAgent 请求失败（HTTP {response.status_code}）')
            run = response.json()
            if not isinstance(run, dict):
                raise WorkflowError('HiAgent 响应必须是 JSON 对象')
            run_id = run.get('runId')
            if isinstance(run_id, str) and run_id.strip() and self.on_started:
                self.on_started(record['generationId'], run_id)
            status = run.get('status')
            if status is not None and status != 'success':
                if status in ('to_start', 'processing', 'failed', 'stopped', 'interrupted', 'timeout'):
                    raise WorkflowError(f'HiAgent 工作流未成功完成（{status}）')
                raise WorkflowError('HiAgent 响应中的工作流状态无效')
            output = run.get('output')
            if not isinstance(output, str) or not output.strip():
                raise WorkflowError('HiAgent 响应缺少有效的 output 字符串')
            outputs = json.loads(output)
            if outputs is None:
                raise WorkflowError('HiAgent 未返回工作流结果（output 为 null）')
            if not isinstance(outputs, dict) or not all(
                    isinstance(outputs.get(key), dict) for key in ('result', 'callbackResult')):
                raise WorkflowError('HiAgent 缺少完整业务结果')
            event('HiAgent 工作流结果已收到', record, workflow_run_id=run_id if isinstance(run_id, str) else None,
                  workflow_status=status if isinstance(status, str) else None,
                  elapsed_ms=round((perf_counter() - started) * 1000, 2))
            return outputs
        except WorkflowError:
            raise
        except (httpx.HTTPError, asyncio.CancelledError) as error:
            event('HiAgent HTTP 传输异常', record, level=logging.WARNING,
                  error_type=type(error).__name__, causes=network_causes(error),
                  phase=phase, status_code=status_code,
                  elapsed_ms=round((perf_counter() - started) * 1000, 2),
                  read_timeout_s=self.settings.read_timeout, connect_timeout_s=10)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise WorkflowError('HiAgent 请求超时') from None
        except (ValueError,TypeError,KeyError,AttributeError):
            raise WorkflowError('HiAgent 响应或业务结果不是有效的 JSON 数据') from None
