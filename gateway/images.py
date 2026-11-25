"""Download mirrored approval PDFs before handing them to the existing converter."""
import asyncio
from contextlib import asynccontextmanager
import logging
from time import perf_counter
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx

from .config import IMAGE_ROUTE, endpoint_urls
from .service_log import event


MAX_PDF_BYTES = 10 * 1024 * 1024
IMAGE_ATTEMPT_SECONDS = 30


class ImageDownloadError(Exception):
    pass


class TaskImages:
    def __init__(self, settings):
        self.bases = endpoint_urls(settings.image_platform_download_base_url, 'IMAGE_PLATFORM_DOWNLOAD_BASE_URL')
        self.au = settings.image_platform_au
        self.files = {}
        self.gateway_url = ''
        if len(self.bases) > 1:
            gateway_urls = endpoint_urls(settings.image_platform_gateway_url, 'IMAGE_PLATFORM_GATEWAY_URL')
            if len(gateway_urls) != 1 or not self.au.strip():
                raise ValueError('影像多地址需要配置一个 IMAGE_PLATFORM_GATEWAY_URL 和 IMAGE_PLATFORM_AU')
            self.gateway_url = gateway_urls[0].rstrip('/')

    @asynccontextmanager
    async def prepare(self, http, record):
        source = record['businessData'].get('replyImageUrl', '')
        if len(self.bases) < 2 or source.lower().startswith(('http://', 'https://')):
            yield source
            return

        parts = source.removeprefix('/').split('/')
        if (len(parts) < 5 or any(not part.strip() or part in ('.', '..') for part in parts)
                or '\\' in source or '://' in source
                or any(ord(char) < 32 or ord(char) == 127 for char in source)):
            raise ImageDownloadError('批复影像相对路径无效')
        path = '/'.join(quote(part, safe='') for part in parts)
        ticket = uuid4().hex + '.pdf'
        self.files[ticket] = await self.download(http, path, record)
        try:
            yield self.gateway_url + IMAGE_ROUTE + ticket
        finally:
            self.files.pop(ticket, None)

    async def download(self, http, path, record):
        query = urlencode({'au': self.au})
        for attempt, base in enumerate(self.bases, 1):
            started = perf_counter()
            status_code = None
            event('批复影像开始下载', record, attempt=attempt, endpoint_count=len(self.bases))
            try:
                async with asyncio.timeout(IMAGE_ATTEMPT_SECONDS):
                    async with http.stream('GET', f'{base.rstrip("/")}/{path}?{query}',
                                           headers={'Accept-Encoding': 'identity'},
                                           timeout=httpx.Timeout(IMAGE_ATTEMPT_SECONDS, connect=10)) as response:
                        status_code = response.status_code
                        if not response.is_success:
                            raise ImageDownloadError('影像接口返回非成功状态')
                        content = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            if len(content) + len(chunk) > MAX_PDF_BYTES:
                                raise ImageDownloadError('批复 PDF 超过 10 MiB')
                            content.extend(chunk)
                        if not content.startswith(b'%PDF-'):
                            raise ImageDownloadError('批复影像响应不是 PDF')
                event('批复影像下载完成', record, attempt=attempt, status_code=status_code,
                      response_bytes=len(content), duration_ms=round((perf_counter() - started) * 1000, 2))
                return bytes(content)
            except (httpx.HTTPError, TimeoutError, ImageDownloadError) as error:
                event('批复影像下载失败', record, level=logging.WARNING, attempt=attempt,
                      endpoint_count=len(self.bases), status_code=status_code, error_type=type(error).__name__,
                      reason=str(error) if isinstance(error, ImageDownloadError) else None,
                      retry=attempt < len(self.bases), duration_ms=round((perf_counter() - started) * 1000, 2))
        raise ImageDownloadError('无法下载批复 PDF，已尝试配置中的影像地址')
