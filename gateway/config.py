"""Literal deployment settings; no model credentials are needed by the gateway."""
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CALLBACK_URL = 'http://10.240.184.200:9090/NLSC/LdsAiTaskESBFunctionApi/0.1.0/ldaAiTaskResultCallback'
CONFIG_NAMES = {
    'DIFY_API_URL', 'DIFY_API_KEY', 'CALLBACK_URL',
    'IMAGE_PLATFORM_DOWNLOAD_BASE_URL', 'IMAGE_PLATFORM_AU',
}
# Previously delivered files remain readable during upgrade; obsolete options have no effect.
LEGACY_NAMES = {
    'TASK_STORAGE_ROOT', 'DIFY_READ_TIMEOUT_SECONDS', 'RULES_DIR',
    'CALLBACK_TIMEOUT_SECONDS', 'CHECKLIST_GATEWAY_URL', 'FILE_SERVICE_API_KEY',
    'API_PORT', 'RULES_PORT', 'APP_NAME', 'DEPLOY_DIR', 'IMAGE_TAG', 'CLOUD_PROXY_PORT',
    'CLOUD_TUNNEL_DNS', 'DIFY_MODEL_PROVIDER', 'DIFY_MODEL_NAME',
}


def read_configuration(config_path=None):
    """Read one literal env file. OS environment values have priority."""
    path = Path(config_path) if config_path is not None else ROOT / 'gateway.env'
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        if config_path is not None:
            raise
        lines = []
    values = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or key not in CONFIG_NAMES | LEGACY_NAMES or key in values:
            raise ValueError(f'gateway.env 第 {number} 行格式错误')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'gateway.env 第 {number} 行引号未闭合')
            value = value[1:-1]
        values[key] = value
    values.update({key: os.environ[key] for key in CONFIG_NAMES if key in os.environ})
    return {key: value for key, value in values.items() if key in CONFIG_NAMES}


@dataclass(frozen=True)
class Settings:
    api_url: str = ''
    api_key: str = field(default='', repr=False)
    storage_root: Path = ROOT / 'gateway_storage'
    read_timeout: float = 1800
    rules_dir: Path = ROOT / 'rules'
    image_platform_download_base_url: str = ''
    image_platform_au: str = field(default='', repr=False)
    callback_url: str = DEFAULT_CALLBACK_URL

    @classmethod
    def from_env(cls, config_path=None):
        values = read_configuration(config_path)
        return cls(api_url=values.get('DIFY_API_URL', ''),
                   api_key=values.get('DIFY_API_KEY', ''),
                   image_platform_download_base_url=values.get('IMAGE_PLATFORM_DOWNLOAD_BASE_URL', ''),
                   image_platform_au=values.get('IMAGE_PLATFORM_AU', ''),
                   callback_url=values.get('CALLBACK_URL', '').strip() or DEFAULT_CALLBACK_URL)

    def require_dify(self):
        import math
        try:
            url = urlsplit(self.api_url)
            valid = (url.scheme in ('http','https') and url.hostname and not url.username
                     and not url.password and not url.query and not url.fragment)
            url.port
        except ValueError:
            valid = False
        if (not valid or not self.api_key.strip() or self.api_key.startswith('<')
                or any(c.isspace() for c in self.api_key) or not math.isfinite(self.read_timeout)
                or self.read_timeout <= 0):
            raise ValueError('请配置有效的 DIFY_API_URL 和 DIFY_API_KEY')
