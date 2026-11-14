"""Exercise offline delivery and failure boundaries without Docker or a cluster."""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_k8s_delivery import build, prepare_bank
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
        if obj['kind']=='Deployment':
            assert obj['spec']['template']['spec']['securityContext']['supplementalGroups']==[0]
        if obj['kind']=='Pod':
            assert obj['spec']['securityContext']['supplementalGroups']==[0]
            assert obj['spec']['containers'][0]['securityContext']['runAsUser']==0
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
        (self.root / 'deploy' / 'k8s' / 'images-sit.txt').write_text(
            '10.208.11.33/agents/shb/material-checklist-dify:test-01\n')
        return build(self.image, 'test-01', root=self.root, split_bytes=split_bytes)

    def configure_environment(self, environment):
        image_ref = f'harbor-{environment}.example:8443/team/material-checklist-dify:v7'
        (self.root / 'deploy' / 'k8s' / f'images-{environment}.txt').write_text(image_ref + '\n')
        manifest = self.root / 'k8s' / environment / 'material-checklist.yaml'
        manifest.write_text(re.sub(
            r'^  CALLBACK_URL:.*$', f'  CALLBACK_URL: "http://cp-{environment}.example/callback"',
            manifest.read_text(), flags=re.MULTILINE))
        return image_ref


class DeliveryTests(DeliveryFixture, unittest.TestCase):
    def test_archive_is_independent_and_images_rules_match(self):
        output = self.package()
        self.assertEqual({p.name for p in output.iterdir()}, {
            'run-all.sh', 'material-checklist-deploy.tar.gz', 'material-checklist-images.tar.gz'})
        with tarfile.open(output / 'material-checklist-deploy.tar.gz') as archive:
            names = archive.getnames()
            self.assertTrue(all(n.startswith('material-checklist-deploy/') for n in names))
            self.assertFalse(any('~$' in n or 'gateway.env.local' in n for n in names))
            self.assertFalse(any(n.endswith(('.sha256', 'SHA256SUMS')) for n in names))
            manifest = archive.extractfile('material-checklist-deploy/k8s/material-checklist.yaml').read()
            self.assertIn(b'material-checklist-dify:test-01', manifest)
            self.assertEqual(archive.extractfile('material-checklist-deploy/images-sit.txt').read(),
                             b'10.208.11.33/agents/shb/material-checklist-dify:test-01\n')
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

    def test_invalid_environment_and_image_fail_before_output(self):
        with self.assertRaises(ValueError):
            build(self.image, 'v1', root=self.root, environment='../sit')
        for value in ('', 'FIXME_HARBOR/team/app:v1', 'harbor.example/team/app',
                      'harbor.example/team/app:v1\nharbor.example/team/app:v2'):
            with self.subTest(value=value):
                (self.root / 'deploy' / 'k8s' / 'images-sit.txt').write_text(value)
                with self.assertRaises(ValueError):
                    build(self.image, 'v1', root=self.root)
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

    def test_bootstrap_assigns_storage_to_bank_appuser(self):
        data = self.root / 'data'
        (data / 'gateway_storage').mkdir(parents=True)
        database = data / 'gateway_storage' / 'gateway.sqlite3'
        database.write_bytes(b'old-data')
        account = SimpleNamespace(pw_uid=12345, pw_gid=12346)
        with mock.patch('deploy.k8s.bootstrap.pwd.getpwnam', return_value=account), \
                mock.patch('deploy.k8s.bootstrap.shutil.chown') as chown:
            prepare(data, rules_archive())
        chown.assert_any_call(data / 'gateway_storage', user=12345, group=12346)
        chown.assert_any_call(database, user=12345, group=12346)
        self.assertEqual(database.read_bytes(), b'old-data')


class BankBuildTests(DeliveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for name in ('Dockerfile.bank', '.dockerignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for name in ('gateway', 'service', 'tools'):
            shutil.copytree(ROOT / name, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
        self.wheels = self.root / 'downloads'
        self.wheels.mkdir()
        (self.wheels / 'fixture-1.0-py3-none-any.whl').write_bytes(b'offline-wheel')
        # Inputs outside the allowlist must never enter the transferable source package.
        (self.root / 'gateway.env').write_text('PRIVATE=do-not-package')
        (self.root / 'gateway' / 'local.env').write_text('PRIVATE=do-not-package')
        self.archive = prepare_bank(self.wheels, 'test-bank', root=self.root)
        unpack = self.root / 'server-33'
        unpack.mkdir()
        with tarfile.open(self.archive) as archive:
            archive.extractall(unpack, filter='data')
        self.source = unpack / 'material-checklist-bank'
        self.bindir = self.root / 'mock-bin'
        self.bindir.mkdir()
        self.log = self.root / 'build-calls.jsonl'
        docker = self.bindir / 'docker'
        docker.write_text('#!' + sys.executable + '\n' + r'''
import json, os, subprocess, sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['BUILD_LOG'],'a') as f: f.write(json.dumps(args)+'\n')
if args[0]=='save': sys.stdout.buffer.write(b'saved-bank-image')
if args[0]=='run' and 'tools/build_k8s_delivery.py' in args:
    mount=args[args.index('-v')+1].removesuffix(':/workspace')
    start=args.index('tools/build_k8s_delivery.py')
    sys.exit(subprocess.call([sys.executable,'-B',*args[start:]],cwd=mount))
''')
        docker.chmod(0o755)
        for name in ('python3', 'git'):
            path = self.bindir / name
            path.write_text('#!/bin/sh\necho "宿主机不应调用此工具" >&2\nexit 99\n')
            path.chmod(0o755)
        self.env = os.environ | {'PATH': str(self.bindir) + os.pathsep + os.environ['PATH'],
                                 'BUILD_LOG': str(self.log)}
        self.env.pop('VERSION', None)
        self.env.pop('BANK_BASE', None)
        self.env.pop('DEPLOY_ENV', None)

    def test_source_bundle_has_linux_wheels_and_no_local_config(self):
        with tarfile.open(self.archive) as archive:
            names = archive.getnames()
        self.assertTrue(all(n.startswith('material-checklist-bank/') for n in names))
        self.assertFalse(any(n.endswith('gateway.env') or n.endswith('local.env') for n in names))
        self.assertTrue((self.source / 'Dockerfile.bank').is_file())
        self.assertEqual((self.source / 'deploy' / 'k8s' / 'images-sit.txt').read_bytes(),
                         (ROOT / 'deploy' / 'k8s' / 'images-sit.txt').read_bytes())
        self.assertFalse((self.source / 'Dockerfile').exists())
        self.assertEqual((self.source / 'BANK_VERSION').read_text().strip(), 'test-bank')
        self.assertEqual((self.source / 'BANK_ENV').read_text().strip(), 'sit')
        self.assertFalse(any(n.endswith(('.sha256', 'SHA256SUMS')) for n in names))
        self.assertFalse(self.archive.with_name(self.archive.name + '.sha256').exists())
        self.assertTrue(list((self.source / 'offline' / 'wheelhouse').glob('*.whl')))

    def test_bank_build_uses_prepared_version_base_and_container_python(self):
        result = subprocess.run(['bash', 'deploy/k8s/make-package.sh'], cwd=self.source,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        build_call = next(c for c in calls if c[0] == 'build')
        self.assertEqual(Path(build_call[build_call.index('-f') + 1]).resolve(),
                         (self.source / 'Dockerfile.bank').resolve())
        self.assertIn('BANK_BASE=10.208.11.33/k8s/kylinv10sp3-python312-ssh:latest', build_call)
        self.assertNotIn('--provenance=false', build_call)
        self.assertFalse(any(c[0] == 'push' for c in calls))
        output = self.source / 'dist' / 'k8s-test-bank'
        self.assertEqual({p.name for p in output.iterdir()}, {
            'run-all.sh', 'material-checklist-deploy.tar.gz', 'material-checklist-images.tar.gz'})

    def test_missing_wheels_stop_before_docker_build(self):
        for wheel in (self.source / 'offline' / 'wheelhouse').glob('*.whl'):
            wheel.unlink()
        result = subprocess.run(['bash', 'deploy/k8s/make-package.sh'], cwd=self.source,
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_build_defaults_to_v1_without_git_or_prepared_version(self):
        (self.source / 'BANK_VERSION').unlink()
        (self.source / 'BANK_ENV').unlink()
        result = subprocess.run(['bash', 'deploy/k8s/make-package.sh'], cwd=self.source,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        build_call = next(c for c in calls if c[0] == 'build')
        self.assertIn('10.208.11.33/agents/shb/material-checklist-dify:v1', build_call)
        with tarfile.open(self.source / 'dist' / 'k8s-v1' / 'material-checklist-deploy.tar.gz') as archive:
            self.assertEqual(archive.extractfile('material-checklist-deploy/images-sit.txt').read(),
                             b'10.208.11.33/agents/shb/material-checklist-dify:v1\n')

    def test_pi_uat_and_prod_use_exact_image_from_environment_list(self):
        for environment in ('pi', 'uat', 'prod'):
            with self.subTest(environment=environment):
                image_ref = self.configure_environment(environment)
                archive_path = prepare_bank(self.wheels, 'v2', root=self.root, environment=environment)
                self.assertEqual(archive_path.name, f'material-checklist-bank-{environment}-v2.tar.gz')
                unpack = self.root / environment
                unpack.mkdir()
                with tarfile.open(archive_path) as archive:
                    archive.extractall(unpack, filter='data')
                source = unpack / 'material-checklist-bank'
                self.assertEqual({p.name for p in (source / 'k8s').iterdir()}, {environment})
                self.assertEqual({p.name for p in (source / 'deploy' / 'k8s').glob('images-*.txt')},
                                 {f'images-{environment}.txt'})
                result = subprocess.run(['bash', 'deploy/k8s/make-package.sh'], cwd=source,
                                        env=self.env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                build_call = [c for c in calls if c[0] == 'build'][-1]
                self.assertEqual(build_call[build_call.index('-t') + 1], image_ref)
                self.assertIn(['save', image_ref], calls)
                output = source / 'dist' / f'k8s-{environment}-v2'
                with tarfile.open(output / 'material-checklist-deploy.tar.gz') as archive:
                    prefix = 'material-checklist-deploy/'
                    self.assertEqual(archive.extractfile(prefix + f'images-{environment}.txt').read(),
                                     (image_ref + '\n').encode())
                    self.assertEqual(archive.extractfile(prefix + 'ENVIRONMENT').read(),
                                     (environment + '\n').encode())
                    manifest = archive.extractfile(prefix + 'k8s/material-checklist.yaml').read().decode()
                    self.assertIn('image: ' + image_ref, manifest)
                    self.assertIn(f'http://cp-{environment}.example/callback', manifest)
                    self.assertNotIn('FIXME_IMAGE_FROM_LIST', manifest)
                    self.assertNotIn('10.208.11.33', manifest)

    def test_unfilled_harbor_stops_before_docker_build(self):
        (self.source / 'deploy' / 'k8s' / 'images-sit.txt').write_text('FIXME_HARBOR/team/app:v1\n')
        result = subprocess.run(['bash', 'deploy/k8s/make-package.sh'], cwd=self.source,
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())


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

    def test_pi_uat_and_prod_entry_push_and_deploy_image_from_environment_list(self):
        for environment in ('pi', 'uat', 'prod'):
            with self.subTest(environment=environment):
                image_ref = self.configure_environment(environment)
                self.output = build(self.image, 'v2', root=self.root, environment=environment)
                with tarfile.open(self.output / 'material-checklist-deploy.tar.gz') as archive:
                    archive.extractall(self.output, filter='data')
                self.deploy = self.output / 'material-checklist-deploy'
                manifest = self.deploy / 'k8s' / 'material-checklist.yaml'
                manifest.write_text(manifest.read_text().replace(
                    '<完整HiAgent同步接口URL>', f'http://hiagent-{environment}.example/sync_run_app_workflow'
                ).replace('<已发布应用的APIKey>', 'test-key'))
                self.statefile.write_text('{}')
                self.log.write_text('')
                self.env['DEPLOY_ENV'] = 'sit'  # The packaged environment determines the image list.
                self.run_script()
                self.assertIn(['docker', 'push', image_ref], self.calls())
                state = json.loads(self.statefile.read_text())
                deployment = state['objects']['deployment/material-checklist-gateway']
                self.assertEqual(deployment['spec']['template']['spec']['containers'][0]['image'], image_ref)
                self.assertEqual(deployment['metadata']['namespace'], 'agent2')

    def test_unfilled_callback_stops_before_apply(self):
        self.manifest.write_text(self.manifest.read_text().replace(
            'http://10.240.184.200:9090/NLSC/LdsAiTaskESBFunctionApi/0.1.0/ldaAiTaskResultCallback',
            '<PI环境CP回调完整URL>'))
        self.run_script('deploy-all.sh', success=False)
        self.assertFalse(any('apply' in c for c in self.calls()))

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
        shutil.rmtree(self.deploy)
        self.run_script()
        self.assertTrue((self.deploy / '.runtime' / 'material-checklist.yaml').is_file())

    def test_missing_images_stop_before_docker_load(self):
        for part in self.output.glob('*.part-*'):
            part.unlink()
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
