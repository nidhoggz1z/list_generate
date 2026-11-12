"""Build a self-contained application archive from this project and a saved image."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()


def build(image, *, output=None, image_tag='material-checklist-dify:1.0',include_workflow=True,split_parts=True):
    image=Path(image).resolve()
    output=Path(output or ROOT/'dist'/'material-checklist-dify-1.0').resolve()
    if not output.is_relative_to((ROOT/'dist').resolve()) or output==(ROOT/'dist').resolve():
        raise ValueError('Output must be a child directory of this project dist')
    if not image.is_file() or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:-]*',image_tag):
        raise ValueError('A saved image and valid image tag are required')
    output.mkdir(parents=True,exist_ok=True)
    names=['start.sh','stop.sh','show.sh','release.sh','deploy-common.sh',
           'gateway.env.example','DEPLOYMENT.md']
    if include_workflow:
        names += ['WORKFLOW_TEST.md','workflows/材料清单-重构.yml',
                  'docs/高码包装部署说明.md','docs/2026-11-03-引文空白修复.md','tools/start_workflow_test.ps1',
                  'examples/workflow-test/businessData.json','examples/workflow-test/approval.pdf']
    names += [str(path.relative_to(ROOT)).replace('\\','/') for path in sorted((ROOT/'rules').iterdir())
              if path.is_file() and not path.name.startswith('~$') and
              (path.name.endswith('材料清单.docx') or path.name=='话术库对应材料.xlsx')]
    if not any(name.endswith('材料清单.docx') for name in names) or 'rules/话术库对应材料.xlsx' not in names:
        raise ValueError('Complete Word and phrase rules are required')
    with tempfile.TemporaryDirectory(prefix='.pack-',dir=output) as temporary:
        stage=Path(temporary)
        for name in names:
            source=(ROOT/name).resolve()
            if not source.is_relative_to(ROOT.resolve()) or not source.is_file():
                raise ValueError('Missing or external package input: '+name)
            target=stage/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        if not include_workflow:
            deployment=stage/'DEPLOYMENT.md'
            note=('本包只交付高码与规则服务。Dify 工作流由你单独导入已有平台。'
                  '实际 gateway.env、AU 和 API Key 由目标机现场配置。\n\n')
            deployment.write_text(note+deployment.read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
        shutil.copyfile(image,stage/'image.tar.gz')
        (stage/'image-name.txt').write_text(image_tag+'\n',encoding='utf-8',newline='\n')
        manifest=dict(createdAt=datetime.now(timezone.utc).isoformat(),project='material-checklist-dify',
                      services=['gateway'],workflowPlatformIncluded=False,highcodeModelCalls=False,
                      image=image_tag,workflowIncluded=include_workflow,
                      workflow='workflows/材料清单-重构.yml' if include_workflow else None,
                      gatewayAuth='Dify application API key')
        (stage/'delivery-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
        payload=sorted(p for p in stage.rglob('*') if p.is_file())
        (stage/'SHA256SUMS').write_text(''.join(digest(p)+'  '+p.relative_to(stage).as_posix()+'\n'
                                                for p in payload),encoding='utf-8',newline='\n')
        archive=stage/'huanban.tar'
        with tarfile.open(archive,'w') as result:
            for path in sorted(p for p in stage.rglob('*') if p.is_file() and p!=archive):
                info=result.gettarinfo(str(path),arcname=path.relative_to(stage).as_posix())
                info.mode=0o755 if path.suffix=='.sh' else 0o600 if path.name=='gateway.env.example' else 0o644
                info.uid=info.gid=0
                info.uname=info.gname=''
                with path.open('rb') as data:
                    result.addfile(info,data)
        published=output/'huanban.tar'
        archive.replace(published)
    for previous in output.glob('huanban.tar_[0-9][0-9][0-9]'):
        previous.unlink()
    parts=[]
    with published.open('rb') as source:
        number=0
        while True:
            block=source.read(1024*1024)
            if not block or not split_parts:
                break
            part=output/('huanban.tar_'+str(number).zfill(3))
            with part.open('wb') as target:
                target.write(block)
                for _ in range(1023):
                    block=source.read(1024*1024)
                    if not block:
                        break
                    target.write(block)
            parts.append(part)
            number+=1
    (output/'SHA256SUMS').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in [published,*parts]),encoding='utf-8',newline='\n')
    print('Offline application package: '+str(published))
    return published


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--image-tag',default='material-checklist-dify:1.0')
    parser.add_argument('--without-workflow',action='store_true')
    parser.add_argument('--no-split',action='store_true')
    args=parser.parse_args()
    build(args.image,image_tag=args.image_tag,include_workflow=not args.without_workflow,
          split_parts=not args.no_split)
