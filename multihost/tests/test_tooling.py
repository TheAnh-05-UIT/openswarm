"""Kiểm thử cục bộ với SWOP v2 có sẵn và file tạm; không gọi Docker."""
import contextlib
import ast
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tooling as tool

HAS_SWOP = importlib.util.find_spec('swop') is not None
WORK = Path(os.environ.get('OPENSWARM_TEST_WORK', ROOT / 'tests/tmp'))
WORK.mkdir(parents=True, exist_ok=True)


def fixture():
    """IP/RUN_ID giả dành cho kiểm thử, không phải thông số board thực tế."""
    data = json.loads((ROOT / 'profile.template.json').read_text(encoding='utf-8'))
    data['deployment']['run_id'] = '20261102t120000z-009b0001'
    data['job_id'] = 'mh-' + data['deployment']['run_id']
    for host, limits, ip in zip(data['hosts'], data['deployment']['hosts'], ('10.231.10.1', '10.231.10.2')):
        host['address'] = ip
        limits['app_bind_ip'] = ip
    return data


@unittest.skipUnless(HAS_SWOP, 'Existing SWOP v2 package required; no alternative schema is used')
class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=WORK)
        self.root = Path(self.tmp.name)
        assert self.root.resolve().is_relative_to(WORK.resolve())
        (self.root / 'assets').mkdir()
        (self.root / 'assets/initial.iutfw').write_bytes((ROOT / 'assets/initial.iutfw').read_bytes())
        self.data = fixture()
        self.profile = self.root / 'profile.json'
        self.save()

    def tearDown(self):
        # Chỉ thư viện chuẩn dọn thư mục tạm vừa tạo bởi chính test này;
        # không xóa tài nguyên Docker, mã ứng dụng hoặc dữ liệu triển khai cũ.
        self.tmp.cleanup()

    def save(self):
        self.profile.write_bytes(tool.encoded(self.data))

    def prepare(self, name='bundle'):
        destination = self.root / name
        metadata = tool.prepare(self.profile, destination)
        return destination, metadata['bundle_id']

    def reject_change(self, change):
        change(self.data)
        self.save()
        with self.assertRaises(ValueError):
            tool.validate_profile(self.profile)

    def mutate_file(self, path, raw):
        path.chmod(0o644)
        path.write_bytes(raw)

    def test_valid_profile_uses_actual_swop_validator(self):
        from swop.profile import load_profile
        with patch('swop.profile.load_profile', wraps=load_profile) as validator:
            result = tool.validate_profile(self.profile)
            validator.assert_called_once_with(self.profile.absolute())
        self.assertEqual(result.data['schema_version'], 2)
        self.assertEqual(result.data['job_id'], self.data['job_id'])

    def test_template_deliberately_requires_operator_inputs(self):
        with self.assertRaises(ValueError):
            tool.validate_profile(ROOT / 'profile.template.json')

    def test_missing_invalid_run_ids(self):
        for run in ('', 'old-job', '20269999t120000z-009b0001', '20261102t120000z-../bad'):
            with self.subTest(run=run):
                self.data = fixture(); self.data['deployment']['run_id'] = run; self.data['job_id'] = 'mh-' + run; self.save()
                with self.assertRaises(ValueError): tool.validate_profile(self.profile)

    def test_missing_run_key(self):
        self.reject_change(lambda d: d['deployment'].pop('run_id'))

    def test_job_identity_mismatch(self):
        self.reject_change(lambda d: d.update(job_id='unrelated-job'))

    def test_duplicate_peer(self):
        self.reject_change(lambda d: d['services'][2].update(peer_id='sl-a'))

    def test_missing_sn(self):
        self.reject_change(lambda d: d.update(services=[s for s in d['services'] if s['role'] != 'sn']))

    def test_sn_on_wrong_host(self):
        self.reject_change(lambda d: d['services'][0].update(host='opi5-pro'))

    def test_wrong_client_placement(self):
        self.reject_change(lambda d: next(s for s in d['services'] if s['id'] == 'client-b').update(host='rpi5'))

    def test_wrong_client_peer_assignment(self):
        self.reject_change(lambda d: next(s for s in d['services'] if s['id'] == 'client-b').update(sl='sl-a'))

    def test_controller_on_wrong_host(self):
        self.reject_change(lambda d: d['orchestration'].update(host='opi5-pro'))

    def test_disabled_or_unacknowledged_controller(self):
        for field in ('enabled', 'exclusive_owner_acknowledged'):
            self.data = fixture()
            with self.subTest(field=field): self.reject_change(lambda d: d['orchestration'].update({field: False}))

    def test_duplicate_controller_service(self):
        self.reject_change(lambda d: d['services'].append({**copy.deepcopy(d['services'][0]), 'id': 'round-controller'}))

    def test_duplicate_json_controller_key(self):
        raw = tool.encoded(self.data).decode().rstrip()
        self.profile.write_text(raw[:-1] + ',"orchestration": {}}', encoding='utf-8')
        with self.assertRaises(ValueError): tool.validate_profile(self.profile)

    def test_unknown_swop_field(self):
        self.reject_change(lambda d: d['orchestration'].update(automatic_resume=True))

    def test_application_port_conflict(self):
        self.reject_change(lambda d: d['services'][1].update(published_port=d['services'][0]['published_port']))

    def test_controller_port_conflict(self):
        self.reject_change(lambda d: d['orchestration'].update(published_port=d['services'][0]['published_port']))

    def test_non_private_or_unsupported_addresses(self):
        for address in ('8.8.8.8', '127.0.0.1', '0.0.0.0', '169.254.1.1', '100.64.0.1',
                        '192.0.2.10', 'pi.example.invalid', '::1', '010.0.0.1'):
            self.data = fixture()
            self.data['hosts'][0]['address'] = address
            self.data['deployment']['hosts'][0]['app_bind_ip'] = address
            self.save()
            with self.subTest(address=address), self.assertRaises(ValueError): tool.validate_profile(self.profile)

    def test_endpoint_host_conflict(self):
        self.reject_change(lambda d: d['hosts'][1].update(address=d['hosts'][0]['address']))

    def test_bind_advertise_mismatch(self):
        self.reject_change(lambda d: d['deployment']['hosts'][0].update(app_bind_ip='10.231.10.99'))

    def test_wrong_bootstrap_hash(self):
        self.reject_change(lambda d: d['bootstrap'].update(sha256='0' * 64))

    def test_corrupt_bootstrap(self):
        (self.root / 'assets/initial.iutfw').write_bytes(b'not weights')
        with self.assertRaises(ValueError): tool.validate_profile(self.profile)

    def test_bootstrap_traversal(self):
        self.reject_change(lambda d: d['bootstrap'].update(path='../assets/initial.iutfw'))

    def test_bootstrap_wrong_type(self):
        self.reject_change(lambda d: d.update(bootstrap=[]))

    def test_mixed_round_ids(self):
        self.reject_change(lambda d: next(s for s in d['services'] if s['id'] == 'client-b')['params'].update(round_ids=[1]))

    def test_distinct_dataset_and_sample_counts(self):
        for field in ('samples', 'data_seed'):
            self.data = fixture()
            a = next(s for s in self.data['services'] if s['id'] == 'client-a')
            b = next(s for s in self.data['services'] if s['id'] == 'client-b')
            b['params'][field] = a['params'][field]; self.save()
            with self.subTest(field=field), self.assertRaises(ValueError): tool.validate_profile(self.profile)

    def test_unsafe_or_unreviewed_storage(self):
        for value in ('../old', './data/../checkpoints', '/var/lib/docker', './runtime', './other-storage'):
            self.data = fixture()
            with self.subTest(value=value): self.reject_change(lambda d: d['deployment']['hosts'][0].update(checkpoint_root=value))

    def test_task008_project_namespace_rejected(self):
        self.reject_change(lambda d: d['deployment'].update(project_prefix='openswarm'))

    def test_unreviewed_image_rejected(self):
        self.reject_change(lambda d: d['services'][0].update(image='24520115/independent-sn:v9'))

    def test_canonical_bundle_and_real_generator(self):
        from swop.generate import bundle
        with patch('swop.generate.bundle', wraps=bundle) as generator:
            root, bundle_id = self.prepare()
            self.assertGreaterEqual(generator.call_count, 1)
        result = tool.verify(root, bundle_id)
        self.assertEqual(result.metadata['manifest']['controller_host'], 'rpi5')
        self.assertEqual(result.metadata['manifest']['job_id'], self.data['job_id'])
        self.assertEqual(result.metadata['manifest']['profile_sha256'], tool.sha(result.files['profile.json']))
        self.assertEqual(result.metadata['manifest']['bootstrap_sha256'], tool.sha(result.profile.bootstrap))
        self.assertTrue(result.metadata['manifest']['model_id'].startswith('sha256:'))

    def test_deterministic_output_and_key_order(self):
        a, first = self.prepare('first')
        self.profile.write_text(json.dumps(self.data, sort_keys=False, indent=4), encoding='utf-8')
        b, second = self.prepare('second')
        self.assertEqual(first, second)
        self.assertEqual(tool.tree(a), tool.tree(b))

    def test_shared_endpoints_and_receipt_identity(self):
        root, bundle_id = self.prepare(); result = tool.verify(root, bundle_id)
        for host, sid in [('rpi5', 'client-a'), ('opi5-pro', 'client-b')]:
            cfg = json.loads(result.files[host + '/config/' + sid + '.json'])
            self.assertEqual(cfg['job_id'], self.data['job_id'])
            self.assertEqual(cfg['sn_url'], 'http://10.231.10.1:18000')
            self.assertEqual(cfg['controller']['url'], 'http://10.231.10.1:19200')
            self.assertEqual(cfg['controller']['run_id'], self.data['deployment']['run_id'])
        self.assertIn(b'report_completion', result.files['opi5-pro/runtime/user_entry.py'])

    def test_generated_controller_placement(self):
        root, bundle_id = self.prepare(); files = tool.verify(root, bundle_id).files
        a = json.loads(files['rpi5/docker-compose.yaml'])
        b = json.loads(files['opi5-pro/docker-compose.yaml'])
        self.assertIn('round-controller', a['services']); self.assertNotIn('round-controller', b['services'])
        self.assertNotIn('sn', b['services'])
        for doc in (a, b): self.assertTrue(all('build' not in s for s in doc['services'].values()))

    def test_host_import_separation_and_same_bundle(self):
        root, bundle_id = self.prepare()
        for host, own_client, other_client in [('rpi5', 'client-a', 'client-b'), ('opi5-pro', 'client-b', 'client-a')]:
            destination = self.root / host
            receipt = tool.import_host(root, host, destination, bundle_id, ownership='defer')
            self.assertEqual(receipt['bundle_id'], bundle_id)
            self.assertFalse(receipt['ownership']['verified']); self.assertFalse(receipt['deployment_authorized'])
            self.assertTrue((destination / ('deployment/config/' + own_client + '.json')).is_file())
            self.assertFalse((destination / ('deployment/config/' + other_client + '.json')).exists())
            self.assertTrue((destination / ('deployment/data/checkpoints/' + own_client)).is_dir())
            self.assertFalse((destination / ('deployment/data/checkpoints/' + other_client)).exists())
            self.assertEqual((destination / 'deployment/swop-output').exists(), host == 'rpi5')
            self.assertFalse((destination / 'deployment/swop-output/rounds.jsonl').exists())
            tool.verify_local(destination, host, bundle_id)

    def test_prepare_never_overwrites_even_empty_target(self):
        existing = self.root / 'existing'; existing.mkdir()
        with self.assertRaises(ValueError): tool.prepare(self.profile, existing)
        self.assertEqual(list(existing.iterdir()), [])

    def test_import_preserves_existing_checkpoint_data(self):
        root, bundle_id = self.prepare(); existing = self.root / 'old-job'; existing.mkdir()
        marker = existing / 'checkpoint.iutfw'; marker.write_bytes(b'KEEP EXACTLY')
        with self.assertRaises(ValueError): tool.import_host(root, 'rpi5', existing, bundle_id, ownership='defer')
        self.assertEqual(marker.read_bytes(), b'KEEP EXACTLY')

    def test_missing_runtime_artifact(self):
        root, bundle_id = self.prepare()
        (root / 'payload/opi5-pro/runtime/user_entry.py').unlink()
        with self.assertRaises(ValueError): tool.verify(root, bundle_id)

    def test_corrupt_runtime_artifact(self):
        root, bundle_id = self.prepare()
        (root / 'payload/opi5-pro/runtime/user_entry.py').write_bytes(b'corrupted')
        with self.assertRaises(ValueError): tool.verify(root, bundle_id)

    def test_extra_file_or_directory(self):
        for kind in ('file', 'directory'):
            root, bundle_id = self.prepare(kind)
            if kind == 'file': (root / 'unexpected').write_bytes(b'')
            else: (root / 'unexpected').mkdir()
            with self.subTest(kind=kind), self.assertRaises(ValueError): tool.verify(root, bundle_id)

    def test_stale_expected_bundle(self):
        root, bundle_id = self.prepare('old')
        self.data['deployment']['run_id'] = '20261102t120001z-009b0002'
        self.data['job_id'] = 'mh-' + self.data['deployment']['run_id']; self.save()
        newer, newer_id = self.prepare('new')
        self.assertNotEqual(bundle_id, newer_id)
        with self.assertRaises(ValueError): tool.verify(newer, bundle_id)

    def test_mixed_host_artifact_rejected(self):
        first, bundle_id = self.prepare('first')
        self.data['services'][-1]['params']['data_seed'] += 1; self.save()
        second, _ = self.prepare('second')
        name = 'payload/opi5-pro/config/client-b.json'
        (first / name).write_bytes((second / name).read_bytes())
        with self.assertRaises(ValueError): tool.verify(first, bundle_id)

    def test_resealed_modified_launcher_still_rejected(self):
        root, _ = self.prepare()
        name = 'payload/rpi5/runtime/sl_entry.py'
        (root / name).write_bytes(b'print("unreviewed code")\n')
        metadata = json.loads((root / 'bundle.json').read_text(encoding='utf-8'))
        for item in metadata['manifest']['artifacts']:
            if item['path'] == name: item.update(size=(root / name).stat().st_size, sha256=tool.sha((root / name).read_bytes()))
        metadata['bundle_id'] = 'sha256:' + tool.sha(tool.encoded(metadata['manifest']))
        (root / 'bundle.json').write_bytes(tool.encoded(metadata))
        with self.assertRaises(ValueError): tool.verify(root, metadata['bundle_id'])

    def test_duplicate_or_traversal_manifest_path(self):
        for path in ('../escape', '/absolute', 'C:/escape', 'payload/../../escape', 'payload/CON'):
            root, _ = self.prepare('case-' + str(len(list(self.root.glob('case-*')))))
            metadata = json.loads((root / 'bundle.json').read_text(encoding='utf-8'))
            metadata['manifest']['artifacts'][0]['path'] = path
            metadata['bundle_id'] = 'sha256:' + tool.sha(tool.encoded(metadata['manifest']))
            (root / 'bundle.json').write_bytes(tool.encoded(metadata))
            with self.subTest(path=path), self.assertRaises(ValueError): tool.verify(root, metadata['bundle_id'])

    def test_expected_id_required(self):
        root, _ = self.prepare()
        for value in ('', 'auto', '0' * 64):
            with self.subTest(value=value), self.assertRaises(ValueError): tool.verify(root, value)

    def test_archives_are_never_extracted(self):
        for suffix in ('zip', 'tar'):
            file = self.root / ('unsafe.' + suffix)
            if suffix == 'zip':
                with zipfile.ZipFile(file, 'w') as archive: archive.writestr('../escape', b'bad')
            else:
                with tarfile.open(file, 'w') as archive:
                    info = tarfile.TarInfo('../escape'); info.size = 3; archive.addfile(info, io.BytesIO(b'bad'))
            with self.subTest(suffix=suffix), self.assertRaises(ValueError): tool.verify(file, 'sha256:' + '0' * 64)
            self.assertFalse((self.root / 'escape').exists())

    def test_symlink_and_reparse_detection(self):
        target, bundle_id = self.prepare()
        original = Path.lstat
        for info in (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0),
                     SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)):
            def mocked(path, **kwargs):
                return info if path == target else original(path, **kwargs)
            with patch.object(Path, 'lstat', mocked), self.assertRaises(ValueError): tool.verify(target, bundle_id)

    def test_hardlink_or_special_file_detection(self):
        root, bundle_id = self.prepare(); target = root / 'unexpected'
        target.write_bytes(b'x'); original = Path.lstat
        for mode, links in ((stat.S_IFREG | 0o644, 2), (stat.S_IFIFO | 0o644, 1)):
            def mocked(path, **kwargs):
                return SimpleNamespace(st_mode=mode, st_nlink=links, st_file_attributes=0) if path == target else original(path, **kwargs)
            with patch.object(Path, 'lstat', mocked), self.assertRaises(ValueError): tool.verify(root, bundle_id)

    def test_special_profile_file_rejected_before_open(self):
        original = Path.lstat
        def mocked(path, **kwargs):
            return SimpleNamespace(st_mode=stat.S_IFIFO | 0o644, st_nlink=1, st_file_attributes=0) if path == self.profile else original(path, **kwargs)
        with patch.object(Path, 'lstat', mocked), patch.object(tool.os, 'open') as opened:
            with self.assertRaises(ValueError): tool.read_file(self.profile)
            opened.assert_not_called()

    def test_oversize_file_rejected(self):
        root, bundle_id = self.prepare(); (root / 'oversize').write_bytes(b'x' * (tool.MAX_FILE + 1))
        with self.assertRaises(ValueError): tool.verify(root, bundle_id)

    def test_destination_traversal_rejected_without_creation(self):
        with self.assertRaises(ValueError): tool.prepare(self.profile, self.root / '..' / 'escape')

    def test_host_selector_mismatch(self):
        root, bundle_id = self.prepare(); target = self.root / 'local'
        tool.import_host(root, 'rpi5', target, bundle_id, ownership='defer')
        with self.assertRaises(ValueError): tool.verify_local(target, 'opi5-pro', bundle_id)

    def test_local_corruption_and_nonempty_storage(self):
        for label in ('corrupt', 'nonempty'):
            root, bundle_id = self.prepare('bundle-' + label); destination = self.root / label
            tool.import_host(root, 'opi5-pro', destination, bundle_id, ownership='defer')
            if label == 'corrupt':
                self.mutate_file(destination / 'deployment/config/client-b.json', b'{}')
            else:
                (destination / 'deployment/data/checkpoints/client-b/round-1.iutfw').write_bytes(b'old checkpoint')
            with self.subTest(label=label), self.assertRaises(ValueError): tool.verify_local(destination, 'opi5-pro', bundle_id)

    def test_host_b_extra_controller_storage_rejected(self):
        root, bundle_id = self.prepare(); destination = self.root / 'local'
        tool.import_host(root, 'opi5-pro', destination, bundle_id, ownership='defer')
        (destination / 'deployment/swop-output').mkdir()
        with self.assertRaises(ValueError): tool.verify_local(destination, 'opi5-pro', bundle_id)

    def test_partial_outputs_retained_and_not_reused(self):
        destination = self.root / 'partial'; original = tool.write_files
        def fail_on_seal(root, files):
            if 'bundle.json' in files: raise OSError('simulated seal write failure')
            return original(root, files)
        with patch.object(tool, 'write_files', fail_on_seal), self.assertRaises(OSError): tool.prepare(self.profile, destination)
        self.assertTrue(destination.is_dir())
        self.assertFalse((destination / 'bundle.json').exists())
        with self.assertRaises(ValueError): tool.verify(destination, 'sha256:' + '0' * 64)
        with self.assertRaises(ValueError): tool.prepare(self.profile, destination)

    def test_enforced_ownership_never_faked_on_windows(self):
        if os.name == 'posix':
            with patch.object(tool.os, 'geteuid', return_value=1000), patch.object(tool.os, 'getegid', return_value=1000):
                with self.assertRaises(ValueError): tool.ownership_possible('enforce')
        else:
            with self.assertRaises(ValueError): tool.ownership_possible('enforce')

    def test_cli_validate_prepare_verify_import(self):
        outputs = []
        def invoke(arguments):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer): code = tool.main(arguments)
            self.assertEqual(code, 0, buffer.getvalue()); outputs.append(json.loads(buffer.getvalue())); return outputs[-1]
        invoke(['validate', str(self.profile)])
        root = self.root / 'cli-bundle'
        metadata = invoke(['prepare', str(self.profile), '--output', str(root)])
        bundle_id = metadata['bundle_id']
        invoke(['verify', str(root), '--expected-id', bundle_id])
        destination = self.root / 'cli-host'
        invoke(['import-host', str(root), '--expected-id', bundle_id, '--host', 'opi5-pro', '--output', str(destination), '--defer-ownership'])
        invoke(['verify-local', str(destination), '--expected-id', bundle_id, '--host', 'opi5-pro'])

    def test_cli_rejection_does_not_create_output(self):
        destination = self.root / 'should-not-exist'; self.data['job_id'] = 'wrong'; self.save()
        output = io.StringIO()
        with contextlib.redirect_stdout(output): code = tool.main(['prepare', str(self.profile), '--output', str(destination)])
        self.assertEqual(code, 2); self.assertEqual(json.loads(output.getvalue())['status'], 'REJECTED')
        self.assertFalse(destination.exists())

    def test_no_network_docker_subprocess_or_archive_extraction_in_tool(self):
        source = (ROOT / 'tooling.py').read_text(encoding='utf-8')
        imports = {node.names[0].name.split('.')[0] for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)}
        self.assertFalse(imports & {'subprocess', 'socket', 'urllib', 'http', 'docker', 'tarfile', 'zipfile'})
        self.assertNotIn('extractall(', source)

    def test_unknown_host_does_not_create_local_storage(self):
        root, bundle_id = self.prepare(); destination = self.root / 'wrong-host'
        with self.assertRaises(ValueError): tool.import_host(root, 'unknown', destination, bundle_id, ownership='defer')
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
