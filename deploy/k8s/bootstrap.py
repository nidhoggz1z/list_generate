"""Run inside the temporary preparation Pod; never touch another project's data."""
from datetime import datetime, timezone
from pathlib import Path
import os
import pwd
import shutil
import sys
import tarfile
import tempfile

from service.app import RuleStore


def prepare(root, stream, *, backup=False, update=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rules, storage = root / 'rules', root / 'gateway_storage'
    if rules.is_symlink() or storage.is_symlink():
        raise ValueError('数据目录不能是符号链接')
    if backup:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        destination = root / 'backups' / stamp
        destination.mkdir(parents=True)
        with tarfile.open(destination / 'state.tar.gz', 'w:gz') as archive:
            for path in (rules, storage):
                if path.exists():
                    archive.add(path, arcname=path.name)
        print('数据备份：' + str(destination))
    with tempfile.TemporaryDirectory(prefix='.rules-', dir=root) as temporary:
        stage = Path(temporary)
        with tarfile.open(fileobj=stream, mode='r|') as archive:
            for member in archive:
                if member.name in ('.', './') and member.isdir():
                    continue
                name = member.name.removeprefix('./')
                if (not member.isfile() or '/' in name or '\\' in name
                        or name.startswith('~$') or Path(name).name != name
                        or not (name.endswith('材料清单.docx') or name == '话术库对应材料.xlsx')):
                    raise ValueError('规则包内存在非法文件')
                target = stage / name
                if target.exists():
                    raise ValueError('规则包内存在重复文件')
                with archive.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
        RuleStore(stage).files()
        if not (stage / '话术库对应材料.xlsx').is_file():
            raise ValueError('规则包缺少话术库 Excel')
        rules.mkdir(exist_ok=True)
        if update or not any(rules.iterdir()):
            if update:
                # The old deployment has stopped and the complete state was backed up.
                shutil.rmtree(rules)
                rules.mkdir()
            for path in stage.iterdir():
                os.replace(path, rules / path.name)
            print('规则已初始化或更新')
        else:
            print('保留现有非空规则目录')
        RuleStore(rules).files()
    storage.mkdir(exist_ok=True)
    # 麒麟镜像以 appuser 运行；准备 Pod 以 root 写入节点盘，按用户名授权。
    try:
        account = pwd.getpwnam('appuser')
    except KeyError:
        # 原 Docker 镜像仍以 root 运行，不要求存在 appuser。
        account = None
    if account is not None:
        shutil.chown(storage, user=account.pw_uid, group=account.pw_gid)
        for directory, directories, files in os.walk(storage):
            for name in directories + files:
                path = Path(directory) / name
                if not path.is_symlink():
                    shutil.chown(path, user=account.pw_uid, group=account.pw_gid)


if __name__ == '__main__':
    prepare('/data', sys.stdin.buffer, backup='--backup' in sys.argv,
            update='--update-rules' in sys.argv)
