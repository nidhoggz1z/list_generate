"""Assemble the independent agent2 delivery using only the standard library."""
import argparse
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'material-checklist-deploy'
SCRIPTS = ('common.sh', 'install-images.sh', 'push-loaded-images-from-list.sh',
           'deploy-all.sh', 'verify.sh', 'bootstrap.py', 'README.md')


def environment_config(root, environment):
    if environment not in ('sit', 'pi', 'prod'):
        raise ValueError('环境必须为 sit、pi 或 prod')
    image_list = root / 'deploy' / 'k8s' / f'images-{environment}.txt'
    image_ref = image_list.read_text(encoding='utf-8').strip()
    if 'FIXME_' in image_ref or not re.fullmatch(
            r'[a-zA-Z0-9][a-zA-Z0-9./:_-]+:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}', image_ref):
        raise ValueError(f'请在 deploy/k8s/images-{environment}.txt 填写单个带 tag 的完整镜像地址')
    manifest = (root / 'k8s' / environment / 'material-checklist.yaml').read_text(encoding='utf-8')
    images = re.findall(r'^\s*image:\s*(\S+)\s*$', manifest, re.MULTILINE)
    if len(images) != 1 or manifest.count(images[0]) != 1:
        raise ValueError('源清单必须包含一个镜像引用')
    return image_ref, manifest, images[0]


def rule_files(root=ROOT):
    files = sorted(path for path in (root / 'rules').iterdir()
                   if path.is_file() and not path.name.startswith('~$') and
                   (path.name.endswith('材料清单.docx') or path.name == '话术库对应材料.xlsx'))
    if not any(p.name.endswith('材料清单.docx') for p in files) or not any(
            p.name == '话术库对应材料.xlsx' for p in files):
        raise ValueError('rules/ 必须包含 Word 材料清单和话术库对应材料.xlsx')
    if any(p.is_symlink() for p in files):
        raise ValueError('规则文件不能使用符号链接')
    return files


def prepare_bank(wheelhouse, version, *, root=ROOT, environment='sit'):
    """Copy an explicit source allowlist plus target wheels; never run Docker."""
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', version):
        raise ValueError('版本号无效')
    environment_config(root, environment)
    rules = rule_files(root)
    wheels = sorted(Path(wheelhouse).glob('*.whl'))
    if not wheels or any(p.is_symlink() or not p.is_file() for p in wheels):
        raise ValueError('离线 wheelhouse 为空或包含符号链接')
    release = version if environment == 'sit' else environment + '-' + version
    output = root / 'dist' / ('material-checklist-bank-' + release + '.tar.gz')
    if output.exists():
        raise ValueError('准备包已存在，请先备份移走')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bank-prepare-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'material-checklist-bank'
        sources = [root / 'Dockerfile.bank', root / '.dockerignore',
                   root / 'tools' / 'build_k8s_delivery.py']
        sources += [root / 'deploy' / 'k8s' / name for name in
                    (*SCRIPTS, 'run-all.sh', 'make-package.sh', f'images-{environment}.txt')]
        sources += [root / 'k8s' / environment / name for name in
                    ('00-namespace.yaml', 'material-checklist.yaml')] + rules
        for directory in ('gateway', 'service'):
            sources += [p for p in (root / directory).rglob('*') if p.is_file() and
                        (p.suffix == '.py' or p.name == 'requirements.txt') and
                        '__pycache__' not in p.parts and not p.name.startswith('._')]
        for source in sources:
            if source.is_symlink() or not source.is_file():
                raise ValueError('构建输入缺失或包含符号链接：' + str(source))
            destination = stage / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        for wheel in wheels:
            destination = stage / 'offline' / 'wheelhouse' / wheel.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(wheel, destination)
        (stage / 'BANK_VERSION').write_text(version + '\n', encoding='utf-8')
        (stage / 'BANK_ENV').write_text(environment + '\n', encoding='utf-8')
        archive_path = Path(temporary) / output.name
        with tarfile.open(archive_path, 'w:gz') as archive:
            for path in sorted(p for p in stage.rglob('*') if p.is_file()):
                info = archive.gettarinfo(str(path), arcname='material-checklist-bank/' + path.relative_to(stage).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if path.suffix == '.sh' else 0o600
                with path.open('rb') as source:
                    archive.addfile(info, source)
        archive_path.replace(output)
        output.chmod(0o600)
    print('上传到 11.33 的准备包：' + str(output))
    return output


def build(image, version, *, root=ROOT, split_bytes=1024**3, environment='sit'):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', version):
        raise ValueError('版本号无效')
    if split_bytes <= 0:
        raise ValueError('分片大小必须大于零')
    image_ref, manifest, source_image = environment_config(root, environment)
    files = rule_files(root)
    image = Path(image)
    if not image.is_file() or image.stat().st_size == 0:
        raise ValueError('缺少保存的压缩镜像')
    release = version if environment == 'sit' else environment + '-' + version
    output = root / 'dist' / ('k8s-' + release)
    if output.exists():
        raise ValueError('输出目录已存在，请先备份移走')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.k8s-package-', dir=output.parent) as temporary:
        work = Path(temporary)
        stage = work / PACKAGE
        stage.mkdir(mode=0o700)
        for name in SCRIPTS:
            shutil.copyfile(root / 'deploy' / 'k8s' / name, stage / name)
        shutil.copyfile(root / 'deploy' / 'k8s' / 'run-all.sh', work / 'run-all.sh')
        (stage / 'k8s').mkdir()
        shutil.copyfile(root / 'k8s' / environment / '00-namespace.yaml', stage / 'k8s' / '00-namespace.yaml')
        (stage / 'k8s' / 'material-checklist.yaml').write_text(
            manifest.replace(source_image, image_ref), encoding='utf-8')
        (stage / 'rules').mkdir()
        for path in files:
            shutil.copyfile(path, stage / 'rules' / path.name)
        (stage / f'images-{environment}.txt').write_text(image_ref + '\n', encoding='utf-8')
        (stage / 'ENVIRONMENT').write_text(environment + '\n', encoding='utf-8')
        (stage / 'VERSION').write_text(version + '\n', encoding='utf-8')
        image_name = 'material-checklist-images.tar.gz'
        archive_path = work / (PACKAGE + '.tar.gz')
        with tarfile.open(archive_path, 'w:gz') as archive:
            for path in sorted(p for p in stage.rglob('*') if p.is_file()):
                info = archive.gettarinfo(str(path), arcname=PACKAGE + '/' + path.relative_to(stage).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if path.suffix == '.sh' else 0o600
                with path.open('rb') as source:
                    archive.addfile(info, source)
        shutil.rmtree(stage)
        if image.stat().st_size <= split_bytes:
            shutil.copyfile(image, work / image_name)
        else:
            with image.open('rb') as source:
                index = 0
                while source.tell() < image.stat().st_size:
                    with (work / (image_name + '.part-' + str(index).zfill(4))).open('wb') as part:
                        remaining = split_bytes
                        while remaining:
                            block = source.read(min(1024**2, remaining))
                            if not block:
                                break
                            part.write(block)
                            remaining -= len(block)
                    index += 1
        for path in work.iterdir():
            path.chmod(0o755 if path.suffix == '.sh' else 0o600)
        work.rename(output)
    print('K8s 交付目录：' + str(output))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-rules', action='store_true')
    parser.add_argument('--check-config', action='store_true')
    parser.add_argument('--environment', choices=('sit', 'pi', 'prod'), default='sit')
    parser.add_argument('--prepare-bank', action='store_true')
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--version')
    args = parser.parse_args()
    if args.check_config:
        rule_files()
        environment_config(ROOT, args.environment)
    elif args.check_rules:
        rule_files()
    elif args.prepare_bank and args.wheelhouse and args.version:
        prepare_bank(args.wheelhouse, args.version, environment=args.environment)
    elif args.image and args.version:
        build(args.image, args.version, environment=args.environment)
    else:
        parser.error('需要 --check-rules、--prepare-bank --wheelhouse --version，或 --image --version')
