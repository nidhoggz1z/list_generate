"""POST /api/v1/tasks -> durable queue -> two Dify runs -> latest-only callback."""
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from service.app import InvalidFileName, RuleStore
from .callback import TaskCallback
from .client import DifyClient
from .config import Settings
from .service_log import RequestLogMiddleware,daily_logging,event
from .store import StorageOwnership,TaskStore
from .task_models import CreateTaskRequest,CreateResponse,FileContentRequest
from .worker import TaskWorker


def create_app(settings=None, *, run_workflow=None, callback_transport=None):
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings.from_env()
        with daily_logging(Path(config.storage_root)/'log'), StorageOwnership(config.storage_root):
            store = TaskStore(config.storage_root)
            async with DifyClient(config,on_started=store.save_run_id) as client, TaskCallback(
                    store,callback_url=config.callback_url,transport=callback_transport) as callback:
                worker = TaskWorker(store,run_workflow or client.run,callback.notify)
                app.state.store,app.state.worker,app.state.settings = store,worker,config
                app.state.rules = RuleStore(config.rules_dir)
                for record in store.recover_interrupted():
                    if store.is_latest(record['generationId']):
                        callback.notify(record)
                worker.start()
                event('包装服务启动',concurrency=2)
                try:
                    yield
                finally:
                    app.state.worker = None
                    await worker.stop()
                    await callback.drain()
                    event('包装服务停止')

    app = FastAPI(title='材料清单 Dify 包装服务',version='1.0.0',lifespan=lifespan)
    app.add_middleware(RequestLogMiddleware)

    @app.middleware('http')
    async def require_json(request,call_next):
        if (request.method=='POST' and request.scope['path'].removeprefix(request.scope.get('root_path',''))=='/api/v1/tasks'
                and request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json'):
            return JSONResponse(status_code=400,content={'reply':'请求必须使用 application/json'})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid(request,error):
        # Never echo the request body, URL, extra fields or provider credentials.
        code = 400 if any(e['type']=='json_invalid' for e in error.errors()) else 422
        return JSONResponse(status_code=code,content={'reply':'JSON 格式或业务字段无效'})

    @app.post('/api/v1/tasks',response_model=CreateResponse)
    async def create_task(body:CreateTaskRequest,request:Request):
        request.scope['log_identity'] = dict(bsnEvtId=body.bsnEvtId,taskId=body.taskId)
        if 'application/json' not in request.headers.get('content-type','').lower():
            return JSONResponse(status_code=400,content={'reply':'请求必须使用 application/json'})
        try:
            if run_workflow is None:
                app.state.settings.require_dify()
            record = app.state.store.accept(body.model_dump(mode='json'))
            app.state.worker.notify()
        except ValueError as error:
            return JSONResponse(status_code=503,content={'reply':str(error)})
        except Exception as error:
            event('任务接收失败',level=logging.ERROR,error_type=type(error).__name__)
            return JSONResponse(status_code=503,content={'reply':'任务存储或服务不可用'})
        event('任务已创建',record,status='PENDING')
        return {key:record[key] for key in ('bsnEvtId','userNo','taskId')}|{'autoReply':'task created'}

    @app.get('/health')
    async def health():
        return dict(status='healthy',service='material-checklist-gateway',concurrency=2,tasks=app.state.store.counts())

    @app.get('/v1/checklist-files')
    def list_checklist_files():
        try:
            return dict(files=app.state.rules.files())
        except (OSError, ValueError) as error:
            event('材料清单目录读取失败', level=logging.ERROR,
                  rules_dir=str(app.state.rules.root),
                  error_type=type(error).__name__, reason=str(error))
            return JSONResponse(status_code=503, content={'reply': '材料清单目录不可用'})

    @app.post('/v1/checklist-files/content')
    def read_checklist_file(body: FileContentRequest):
        try:
            return app.state.rules.file_content(body.fileName)
        except InvalidFileName:
            return JSONResponse(status_code=422, content={'reply': '文件名无效'})
        except FileNotFoundError:
            return JSONResponse(status_code=404, content={'reply': '材料清单不存在'})
        except Exception:
            return JSONResponse(status_code=503, content={'reply': '材料清单读取失败'})

    return app


app = create_app()
