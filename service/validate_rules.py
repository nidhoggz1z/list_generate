"""Validate and fingerprint the immutable rules shipped in an image."""
import hashlib
from pathlib import Path

from .app import RuleStore
from .phrase_rules import snapshot_phrase_library


def validate_rules(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('规则目录不存在或为符号链接')
    store = RuleStore(root)
    files = store.files()
    paths = [root / item['fileName'] for item in files]
    paths.append(root / '话术库对应材料.xlsx')
    allowed = {p.name for p in paths}
    unexpected = [p.name for p in root.iterdir() if p.suffix in ('.docx', '.xlsx')
                  and not p.name.startswith('~$') and p.name not in allowed]
    if unexpected:
        raise ValueError('规则目录包含未允许的 Word/Excel 文件: ' + ', '.join(sorted(unexpected)))
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError('规则文件缺失、为空或使用符号链接: ' + path.name)
        digest.update(path.name.encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    for item in files:
        if not store.file_content(item['fileName'])['content'].strip():
            raise ValueError('规则正文为空: ' + item['fileName'])
    snapshot_phrase_library(root)
    return digest.hexdigest()


if __name__ == '__main__':
    print(validate_rules('/app/rules'))
