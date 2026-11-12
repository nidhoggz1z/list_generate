"""Call HiAgent's synchronous workflow API using asynchronous HTTP I/O."""
import json
import httpx

from .service_log import event


class WorkflowError(Exception):
    """A safe error message; raw HTTP/provider responses never leave the client."""


class DifyClient:
    def __init__(self, settings, *, transport=None, on_started=None):
        self.settings = settings
        self.on_started = on_started
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(settings.read_timeout,connect=10),
                                     trust_env=False,transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def run(self, record):
        try:
            business = record['businessData']
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
            event('HiAgent 工作流开始请求', record)
            response = await self.http.post(self.settings.api_url,
                                            headers={'Apikey': self.settings.api_key}, json=body)
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
            return outputs
        except WorkflowError:
            raise
        except httpx.TimeoutException:
            raise WorkflowError('HiAgent 请求超时') from None
        except httpx.HTTPError:
            raise WorkflowError('HiAgent 网络连接失败') from None
        except (ValueError,TypeError,KeyError,AttributeError):
            raise WorkflowError('HiAgent 响应或业务结果不是有效的 JSON 数据') from None
