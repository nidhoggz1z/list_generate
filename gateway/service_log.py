"""Application-only UTF-8 daily logs; never capture HTTP bodies or provider logs."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
from time import perf_counter

BEIJING = timezone(timedelta(hours=8))
DEFAULT_LOG_DIR = Path(__file__).resolve().parent / 'log'
logger = logging.getLogger('material_service')
LOGGERS = (logger, logging.getLogger('task_files'), logging.getLogger('task_worker'))


class BeijingFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, BEIJING).isoformat(sep=' ', timespec='milliseconds')


class DailyLogHandler(logging.FileHandler):
    """Append to YYYY-MM-DD.txt, switching on the first event of each Beijing day."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        filename = self.directory / (datetime.now(BEIJING).strftime('%Y-%m-%d') + '.txt')
        super().__init__(filename, mode='a', encoding='utf-8', delay=True, errors='backslashreplace')
        self.setFormatter(BeijingFormatter('%(asctime)s %(levelname)s %(message)s'))

    def emit(self, record):
        try:
            filename = self.directory / (datetime.fromtimestamp(record.created, BEIJING).strftime('%Y-%m-%d') + '.txt')
            if str(filename) != self.baseFilename:
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                self.baseFilename = str(filename)
            if self.stream is None:
                self.directory.mkdir(parents=True, exist_ok=True)
            super().emit(record)
        except Exception:
            self.handleError(record)

    def handleError(self, record):
        # File permissions/disk failures must not turn successful business work into errors.
        try:
            sys.stderr.write('日志文件写入失败，转至控制台：' + self.format(record) + '\n')
        except (OSError, UnicodeError):
            pass

    def close(self):
        try:
            super().close()
        except (OSError, UnicodeError):
            # FileHandler still closes/removes the stream in its finally blocks.
            pass


@contextmanager
def daily_logging(directory=None):
    directory = Path(directory if directory is not None else DEFAULT_LOG_DIR).resolve()
    if any(isinstance(h, DailyLogHandler) and h.directory == directory for h in logger.handlers):
        yield
        return
    handler = DailyLogHandler(directory)
    levels = [item.level for item in LOGGERS]
    for item in LOGGERS:
        item.addHandler(handler)
        item.setLevel(logging.INFO)
    try:
        yield
    finally:
        for item, level in zip(LOGGERS, levels):
            item.removeHandler(handler)
            item.setLevel(level)
        handler.close()


def event(message, identity=None, *, level=logging.INFO, **details):
    """Only explicitly chosen IDs and summary fields enter logs. Escape line breaks."""
    fields = {key: identity[key] for key in ('bsnEvtId', 'taskId', 'generationId')
              if isinstance(identity, dict) and key in identity}
    fields.update(details)
    values = ' '.join(f'{key}={json.dumps(value[:512] if isinstance(value, str) else value, ensure_ascii=False)}'
                      for key, value in fields.items())
    logger.log(level, '%s%s', message, ' ' + values if values else '')


class RequestLogMiddleware:
    """Observe status and total response time without consuming bodies or streams."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started, status, error_type = perf_counter(), 500, None

        async def observed_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except BaseException as error:
            error_type = type(error).__name__
            raise
        finally:
            route = scope.get('route')
            summary = dict(method=scope['method'], path=getattr(route, 'path', '<unmatched>'),
                           status=status, duration_ms=round((perf_counter() - started) * 1000, 2))
            if error_type:
                summary['error_type'] = error_type
            event('HTTP请求', scope.get('log_identity'),
                  level=logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO,
                  **summary)
