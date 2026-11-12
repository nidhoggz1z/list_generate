"""Exercise offline delivery and failure boundaries without Docker or a cluster."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_k8s_delivery import build
from deploy.k8s.bootstrap import prepare


STUB = r'''
import json, os, sys
from pathlib import Path
import yaml
args=sys.argv[1:]
statefile=Path(os.environ['STUB_STATE'])
state=json.loads(statefile.read_text())
with open(os.environ['STUB_LOG'],'a') as f:
    f.write(json.dumps([Path(sys.argv[0]).name]+args)+'\n')
def save(): statefile.write_text(json.dumps(state))
def done(value=''):
    if value: print(value)
    save(); sys.exit(0)
if Path(sys.argv[0]).name=='docker': done()
if args[:2]==['config','current-context']: done('sit-test')
assert args[:4]==['--context','sit-test','--namespace','agent2'],args
args=args[4:]
verb=args[0]
if verb=='get':
    kind=args[1]
    name=args[2] if len(args)>2 else ''
    query=args[-1]
    if kind=='namespace': done('namespace/agent2' if state.get('namespace') else '')
    if kind=='nodes': done('drained|true|amd64|True\nnot-ready||amd64|False\nworker-b||amd64|True\nworker-a||amd64|True')
    if kind=='node': done('true|amd64|False' if state.get('bad_node') else '|amd64|True')
    if ',' in kind: done('{}')
    obj=state.get('objects',{}).get(kind+'/'+name)
    if not obj: done()
    if query=='name': done(kind+'/'+name)
    if query=='yaml': done(yaml.safe_dump(obj))
    if 'labels.app' in query: done(obj['metadata']['labels']['app.kubernetes.io/part-of'])
    if kind=='deployment':
        spec=obj['spec']; template=spec['template']
        if 'nodeSelector' in query: done(template['spec']['nodeSelector']['kubernetes.io/hostname'])
        if 'rules-sha' in query: done(template['metadata']['annotations']['material-checklist/rules-sha'])
        if 'manifest-sha' in query: done(template['metadata']['annotations']['material-checklist/manifest-sha'])
        if 'spec.replicas' in query: done(str(spec['replicas']))
    done('{}')
if verb in ('apply','create'):
    file=args[args.index('-f')+1]
    content=sys.stdin.read() if file=='-' else Path(file).read_text()
    if '--dry-run=server' in args: done('validated')
    for obj in yaml.safe_load_all(content):
        if obj['kind']=='Namespace':
            state['namespace']=True; continue
        assert obj['metadata']['namespace']=='agent2'
        state.setdefault('objects',{})[obj['kind'].lower()+'/'+obj['metadata']['name']]=obj
    done('created')
if verb=='scale':
    state['objects'][args[1]]['spec']['replicas']=0; done('scaled')
if verb=='exec':
    if args[1]=='-i':
        sys.stdin.buffer.read()
        if state.get('fail_prepare'): save(); sys.exit(1)
    elif state.get('busy') and '仍有未完成任务' in args[-1]:
        print('仍有未完成任务',file=sys.stderr); sys.exit(1)
    done('PASS')
if verb=='delete':
    state.get('objects',{}).pop('pod/'+args[2],None); done()
done('ok')
'''


def rules_archive(value=b'new'):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name in ('产品甲材料清单.docx', '话术库对应材料.xlsx'):
            info = tarfile.TarInfo('./' + name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    stream.seek(0)
    return stream


class DeliveryFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(ROOT / 'deploy', self.root / 'deploy')
        shutil.copytree(ROOT / 'k8s', self.root / 'k8s')
        (self.root / 'rules').mkdir()
        for name in ('产品甲材料清单.docx', '话术库对应材料.xlsx', '~$临时材料清单.docx'):
            (self.root / 'rules' / name).write_bytes(b'fixture')
        self.image = self.root / 'saved-image.gz'
        self.image.write_bytes(b'fake-saved-image' * 8)

    def package(self, *, split_bytes=1024):
        return build(self.image, 'test-01', root=self.root, split_bytes=split_bytes)


class DeliveryTests(DeliveryFixture, unittest.TestCase):
    def test_archive_is_independent_and_images_rules_match(self):
        output = self.package()
        subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=output, check=True, capture_output=True)
        with tarfile.open(output / 'material-checklist-deploy.tar.gz') as archive:
            names = archive.getnames()
            self.assertTrue(all(n.startswith('material-checklist-deploy/') for n in names))
            self.assertFalse(any('~$' in n or 'gateway.env.local' in n for n in names))
            manifest = archive.extractfile('material-checklist-deploy/k8s/material-checklist.yaml').read()
            self.assertIn(b'material-checklist-dify:test-01', manifest)
            self.assertTrue(all(m.mode in (0o600, 0o755) for m in archive.getmembers()))
        with self.assertRaises(ValueError):
            self.package()

    def test_missing_rules_and_invalid_version_fail_before_output(self):
        with self.assertRaises(ValueError):
            build(self.image, '../escape', root=self.root)
        (self.root / 'rules' / '话术库对应材料.xlsx').unlink()
        with self.assertRaises(ValueError):
            self.package()
        self.assertFalse((self.root / 'dist').exists())

    def test_bootstrap_preserves_rules_and_backs_up_before_update(self):
        data = self.root / 'data'
        prepare(data, rules_archive(b'first'))
        (data / 'gateway_storage' / 'gateway.sqlite3').write_bytes(b'existing-database')
        prepare(data, rules_archive(b'second'))
        self.assertEqual((data / 'rules' / '产品甲材料清单.docx').read_bytes(), b'first')
        prepare(data, rules_archive(b'second'), backup=True, update=True)
        self.assertEqual((data / 'rules' / '产品甲材料清单.docx').read_bytes(), b'second')
        self.assertEqual((data / 'gateway_storage' / 'gateway.sqlite3').read_bytes(), b'existing-database')
        with tarfile.open(next((data / 'backups').glob('*/state.tar.gz'))) as archive:
            self.assertEqual(archive.extractfile('rules/产品甲材料清单.docx').read(), b'first')
            self.assertEqual(archive.extractfile('gateway_storage/gateway.sqlite3').read(), b'existing-database')

    def test_bootstrap_rejects_archive_traversal_without_overwrite(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            info = tarfile.TarInfo('../escape材料清单.docx')
            info.size = 1
            archive.addfile(info, io.BytesIO(b'x'))
        stream.seek(0)
        with self.assertRaises(ValueError):
            prepare(self.root / 'data', stream)
        self.assertFalse((self.root / 'escape材料清单.docx').exists())


@unittest.skipUnless(importlib.util.find_spec('yaml'), '需要现有 requirements-build.txt 中的 PyYAML')
class ScriptFlowTests(DeliveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.output = self.package(split_bytes=32)
        with tarfile.open(self.output / 'material-checklist-deploy.tar.gz') as archive:
            archive.extractall(self.output, filter='data')
        self.deploy = self.output / 'material-checklist-deploy'
        self.manifest = self.deploy / 'k8s' / 'material-checklist.yaml'
        self.manifest.write_text(self.manifest.read_text().replace('<完整HiAgent同步接口URL>', 'http://hiagent.test/sync_run_app_workflow').replace('<已发布应用的APIKey>', 'test-key'))
        bindir = self.root / 'bin'
        bindir.mkdir()
        for name in ('docker', 'kubectl'):
            path = bindir / name
            path.write_text('#!' + sys.executable + '\n' + STUB)
            path.chmod(0o755)
        self.statefile = self.root / 'state.json'
        self.statefile.write_text('{}')
        self.log = self.root / 'calls.jsonl'
        self.env = os.environ | {'PATH': str(bindir) + os.pathsep + os.environ['PATH'],
                                 'STUB_STATE': str(self.statefile), 'STUB_LOG': str(self.log)}
        self.env.pop('KUBE_CONTEXT', None)

    def run_script(self, name='run-all.sh', *args, success=True):
        path = self.output / name if name == 'run-all.sh' else self.deploy / name
        result = subprocess.run(['bash', str(path), *args], env=self.env, capture_output=True, text=True, errors='replace')
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def change_state(self, **changes):
        state = json.loads(self.statefile.read_text()) | changes
        self.statefile.write_text(json.dumps(state))

    def test_full_entry_merges_pushes_and_binds_first_eligible_node(self):
        self.run_script()
        self.assertEqual((self.output / 'material-checklist-images.tar.gz').read_bytes(), self.image.read_bytes())
        calls = self.calls()
        load = next(i for i, c in enumerate(calls) if c[:2] == ['docker', 'load'])
        push = next(i for i, c in enumerate(calls) if c[:2] == ['docker', 'push'])
        apply = next(i for i, c in enumerate(calls) if c[0] == 'kubectl' and 'apply' in c)
        self.assertLess(load, push)
        self.assertLess(push, apply)
        state = json.loads(self.statefile.read_text())
        obj = state['objects']['deployment/material-checklist-gateway']
        self.assertEqual(obj['spec']['template']['spec']['nodeSelector']['kubernetes.io/hostname'], 'worker-b')
        self.assertEqual((self.deploy / '.node-name').read_text().strip(), 'worker-b')
        for call in calls:
            if call[0] == 'kubectl' and call[1] != 'config':
                self.assertEqual(call[1:5], ['--context', 'sit-test', '--namespace', 'agent2'])
        self.run_script('verify.sh')

    def test_rerun_has_no_scale_or_prepare_and_upgrade_checks_busy_queue(self):
        self.run_script()
        start = len(self.calls())
        self.run_script()
        self.assertFalse(any('scale' in c or 'create' in c for c in self.calls()[start:]))
        self.manifest.write_text(self.manifest.read_text().replace('9090/NLSC', '9091/NLSC'))
        self.change_state(busy=True)
        start = len(self.calls())
        self.run_script('deploy-all.sh', success=False)
        self.assertFalse(any('scale' in c or ('apply' in c and '--dry-run=server' not in c) for c in self.calls()[start:]))
        self.change_state(busy=False)
        self.run_script('deploy-all.sh')
        self.assertTrue(any('scale' in c for c in self.calls()[start:]))
        self.assertTrue(list((self.deploy / 'backups').glob('*/secret.yaml')))

    def test_fixed_node_failure_does_not_select_another_node(self):
        self.run_script()
        self.change_state(bad_node=True)
        start = len(self.calls())
        self.run_script('deploy-all.sh', success=False)
        self.assertFalse(any('nodes' in c or 'apply' in c for c in self.calls()[start:]))

    def test_prepare_failure_leaves_upgrade_stopped_and_cleans_only_helper(self):
        self.run_script()
        self.manifest.write_text(self.manifest.read_text().replace('9090/NLSC', '9091/NLSC'))
        self.change_state(fail_prepare=True)
        start = len(self.calls())
        self.run_script('deploy-all.sh', success=False)
        state = json.loads(self.statefile.read_text())
        self.assertEqual(state['objects']['deployment/material-checklist-gateway']['spec']['replicas'], 0)
        self.assertFalse(any(name.startswith('pod/material-checklist-prepare-') for name in state['objects']))
        self.assertFalse(any('apply' in c and c[-1].endswith('/.runtime/material-checklist.yaml')
                             and '--dry-run=server' not in c for c in self.calls()[start:]))

    def test_run_all_extracts_package_when_directory_is_absent(self):
        # Put the configured delivery back into the tar, as a preconfigured release.
        archive_path = self.output / 'material-checklist-deploy.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            archive.add(self.deploy, arcname='material-checklist-deploy')
        from tools.build_k8s_delivery import digest
        sums = self.output / 'SHA256SUMS'
        lines = sums.read_text().splitlines()
        sums.write_text('\n'.join(digest(archive_path) + '  ' + archive_path.name
                                 if line.endswith('  ' + archive_path.name) else line
                                 for line in lines) + '\n')
        shutil.rmtree(self.deploy)
        self.run_script()
        self.assertTrue((self.deploy / '.runtime' / 'material-checklist.yaml').is_file())

    def test_tampered_image_is_not_loaded(self):
        part = next(self.output.glob('*.part-*'))
        part.write_bytes(b'broken')
        self.run_script(success=False)
        self.assertFalse(any(c[0] == 'docker' for c in self.calls()))

    def test_unfilled_config_and_foreign_resource_stop_deployment(self):
        self.manifest.write_text(self.manifest.read_text().replace('test-key', '<未填写>'))
        self.run_script('deploy-all.sh', success=False)
        self.assertFalse(any('apply' in c for c in self.calls()))
        self.manifest.write_text(self.manifest.read_text().replace('<未填写>', 'test-key'))
        self.change_state(objects={'service/material-checklist-gateway': {
            'metadata': {'labels': {'app.kubernetes.io/part-of': 'another-project'}}}})
        self.run_script('deploy-all.sh', success=False)
        self.assertFalse(any('apply' in c for c in self.calls()))


@unittest.skipUnless(importlib.util.find_spec('fastapi'), '需要 gateway/requirements.txt')
class RootPathTests(unittest.TestCase):
    def test_proxy_prefix_preserves_json_validation_and_openapi(self):
        from fastapi.testclient import TestClient
        from gateway.config import Settings
        from gateway.main import create_app
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(Settings(storage_root=Path(temporary)))
            with TestClient(app, root_path='/material-checklist') as client:
                for prefix in ('', '/material-checklist'):
                    self.assertEqual(client.get(prefix + '/health').status_code, 200)
                    response = client.post(prefix + '/api/v1/tasks', content='{}',
                                           headers={'content-type': 'text/plain'})
                    self.assertEqual(response.status_code, 400)
                schema = client.get('/material-checklist/openapi.json').json()
                self.assertIn({'url': '/material-checklist'}, schema['servers'])


if __name__ == '__main__':
    unittest.main()
