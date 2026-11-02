"""Kiểm thử cục bộ: Docker thật chỉ dùng Compose config; không sửa tài nguyên Engine."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tooling as tool
import runtime_contract as contract
import preflight
import operations
from test_tooling import fixture, HAS_SWOP, WORK


def labels(ctx, role):
    return {'com.docker.compose.project': ctx.project, 'com.docker.compose.service': role,
            'io.openswarm.rebuild.project': 'osm009', 'io.openswarm.rebuild.owner': 'openswarm-multihost',
            'io.openswarm.rebuild.run': ctx.env['RUN_ID'], 'io.openswarm.rebuild.host': ctx.host,
            'io.openswarm.bundle': ctx.env['BUNDLE_ID'], 'io.openswarm.rebuild.role': role}


class FakeRunner:
    """Ghi thao tác giả trong bộ nhớ; không chuyển tiếp sang subprocess hoặc socket thật."""
    def __init__(self, ctx, model, approved=True):
        self.ctx, self.model, self.approved = ctx, model, approved
        self.items = {}; self.network = False; self.bad_network = False
        self.calls = []; self.changes = []; self.round_exists = False; self.bad_model = False
        self.listeners = ''; self.local_ip = True; self.no_route = False; self.offline_http = False
        self.foreign_ports = []
        self.epoch = 'FIXTURE-NOT-A-PHYSICAL-EPOCH'

    def item(self, role, running=True):
        ref = self.ctx.env['SN_IMAGE'] if role == 'sn' else self.ctx.env['SL_IMAGE'] if role.startswith('sl-') else self.ctx.env['USER_TF_IMAGE'] if role.startswith('client-') else self.ctx.env['SWOP_IMAGE']
        item = {'id': hashlib.sha256(role.encode()).hexdigest(), 'labels': labels(self.ctx, role),
                'state': {'Running': running, 'Status': 'running' if running else 'exited', 'ExitCode': 0, 'Health': {'Status': 'healthy'}},
                'image': ref, 'user': '65532:65532', 'ports': {}}
        key = {'sn': 'SN', 'sl-a': 'SL_A', 'sl-b': 'SL_B', 'round-controller': 'CONTROLLER'}.get(role)
        if key: item['ports'] = {self.ctx.env[key + '_PORT'] + '/tcp': [{'HostIp': self.ctx.env['HOST_A_IP' if self.ctx.host == 'rpi5' else 'HOST_B_IP'], 'HostPort': self.ctx.env[key + '_HOST_PORT']}]}
        self.items[role] = item; return item

    def read(self, args, ctx, timeout=10):
        self.calls.append(args)
        if args[:3] == ['docker', 'compose', 'version']: return 'Docker Compose FIXTURE'
        if args[:2] == ['docker', 'compose'] and 'config' in args: return json.dumps(self.model)
        if args[:3] == ['docker', 'context', 'inspect']: return 'unix:///var/run/docker.sock'
        if args[:2] == ['docker', 'info']: return json.dumps({'os': 'linux', 'architecture': 'aarch64', 'id': 'FIXTURE-' + ctx.host})
        if args[:3] == ['docker', 'container', 'ls']:
            if '--quiet' in args: return '\n'.join(x['id'] for x in self.items.values())
            return '\n'.join(json.dumps(x) for x in self.foreign_ports)
        if args[:3] == ['docker', 'container', 'inspect']: return json.dumps(next(x for x in self.items.values() if x['id'] == args[-1]))
        if args[:3] == ['docker', 'network', 'ls']: return json.dumps(ctx.project + '_lab') if self.network else ''
        if args[:3] == ['docker', 'network', 'inspect']:
            result = labels(ctx, 'network-lab')
            if self.bad_network: result['io.openswarm.rebuild.owner'] = 'foreign'
            return json.dumps(result)
        if args[:3] == ['docker', 'image', 'inspect']: return json.dumps({'os': 'linux', 'architecture': 'arm64', 'user': '65532:65532', 'id': 'FIXTURE-IMAGE-ID'})
        if args[:4] == ['ip', '-j', '-4', 'address']:
            return json.dumps([{'ifname': 'fixture0', 'flags': ['UP'], 'addr_info': [{'local': ctx.env['HOST_A_IP' if ctx.host == 'rpi5' else 'HOST_B_IP']}]}] if self.local_ip else [])
        if args[:4] == ['ip', '-j', '-4', 'route']: return json.dumps([{'type': 'unreachable'}] if self.no_route else [{'dev': 'fixture0'}])
        if args[:3] == ['ss', '-H', '-ltn']: return self.listeners
        raise AssertionError('Unexpected real-command path: ' + repr(args))

    def change(self, args, ctx):
        if not self.approved: raise tool.BundleError('No approval')
        self.changes.append(args); return 'MOCK ONLY; NOT EXECUTED'

    def get(self, url, path, timeout=2):
        self.calls.append(['FIXTURE_GET', url, path])
        if self.offline_http: raise OSError('fixture unavailable')
        if path == '/health':
            if url.endswith(':' + self.ctx.env['CONTROLLER_HOST_PORT']): return 200, {'service': 'swop-round-controller', 'protocol_version': 1}
            return 200, {'service': 'independent-sn', 'api_version': '1', 'status': 'ok', 'storage': 'memory', 'instance_id': self.epoch}
        if path == '/v1/rounds/current': return (200, {'round_id': 1}) if self.round_exists else (404, {'error': {'code': 'round_not_found'}})
        if path.startswith('/v1/peers'):
            return 200, {'instance_id': self.epoch, 'peers': [{'peer_id': r, 'alive': True, 'endpoint': u + '/'} for r, u in preflight.endpoints(self.ctx).items() if r.startswith('sl-')]}
        if path == '/v1/ml/session':
            role = next(k for k, value in preflight.endpoints(self.ctx).items() if value == url)
            return 200, {'protocol_version': 1, 'job_id': self.ctx.manifest['job_id'], 'sn_instance_id': self.epoch,
                         'peer_id': role, 'model_id': 'WRONG' if self.bad_model else self.ctx.manifest['model_id'],
                         'participants': ['sl-a', 'sl-b'], 'round': None,
                         'committed': {'version': 0, 'weights_sha256': self.ctx.manifest['bootstrap_sha256']}}
        raise AssertionError(path)


@unittest.skipUnless(HAS_SWOP, 'Existing SWOP v2 required to create real 009B fixture imports')
class OperationalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(dir=WORK); cls.base = Path(cls.tmp.name)
        (cls.base / 'assets').mkdir(); (cls.base / 'assets/initial.iutfw').write_bytes((ROOT / 'assets/initial.iutfw').read_bytes())
        (cls.base / 'profile.json').write_bytes(tool.encoded(fixture()))
        metadata = tool.prepare(cls.base / 'profile.json', cls.base / 'bundle'); cls.bundle_id = metadata['bundle_id']
        cls.contexts, cls.models = {}, {}
        for host in tool.HOSTS:
            root = cls.base / host; tool.import_host(cls.base / 'bundle', host, root, cls.bundle_id, ownership='defer')
            env = cls.base / (host + '.env'); contract.configure(root, host, cls.bundle_id, env)
            ctx = contract.load_context(host, env); cls.contexts[host] = ctx
            # Docker thật chỉ chạy Compose config, không up/run/pull/stop hay sửa mạng.
            cls.models[host] = operations.render(ctx, preflight.Runner())

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def setUp(self):
        self.ctx = self.contexts['rpi5']; self.runner = FakeRunner(self.ctx, self.models['rpi5'])
        self.platform = patch.object(preflight.platform, 'system', return_value='Linux')
        self.arch = patch.object(preflight.platform, 'machine', return_value='aarch64')
        self.platform.start(); self.arch.start()

    def tearDown(self): self.platform.stop(); self.arch.stop()

    def test_both_compose_models_exact_placement(self):
        for host, data in self.models.items():
            self.assertEqual(set(data['services']), set(contract.ROLES[host]))
            self.assertNotIn('swop', data['services'])
        self.assertNotIn('sn', self.models['opi5-pro']['services'])
        self.assertNotIn('round-controller', self.models['opi5-pro']['services'])

    def test_real_models_match_upstream_runtime(self):
        for host in tool.HOSTS: operations.validate_rendered(self.contexts[host], self.models[host])

    def test_readable_block_yaml_no_host_network_no_build(self):
        for name in ('compose.host-a.yaml', 'compose.host-b.yaml'):
            raw = (ROOT / name).read_text(encoding='utf-8'); self.assertTrue(raw.startswith('name:'))
            self.assertNotIn('build:', raw); self.assertNotIn('network_mode:', raw)
            self.assertNotIn('docker.sock', raw); self.assertNotIn('privileged:', raw)

    def test_no_automatic_training_or_controller(self):
        for host, data in self.models.items():
            for role, service in data['services'].items():
                if role.startswith('client-'): self.assertEqual(service['profiles'], ['training'])
                if role == 'round-controller': self.assertEqual(service['profiles'], ['controller'])

    def test_published_ports_and_private_addresses(self):
        expected = {'sn': (18000, 8000), 'sl-a': (19001, 9000), 'sl-b': (19002, 9000), 'round-controller': (19200, 9200)}
        for data in self.models.values():
            for role, service in data['services'].items():
                for p in service.get('ports', []):
                    self.assertEqual((int(p['published']), p['target']), expected[role]); tool.private_ipv4(p['host_ip'])

    def test_unsafe_bind_rejected(self):
        data = copy.deepcopy(self.models['rpi5']); data['services']['sn']['ports'][0]['host_ip'] = '0.0.0.0'
        with self.assertRaises(ValueError): operations.validate_rendered(self.ctx, data)

    def test_extra_privileges_or_injected_environment_rejected(self):
        for field, value in [('cap_add', ['SYS_ADMIN']), ('pid', 'host')]:
            data = copy.deepcopy(self.models['rpi5']); data['services']['sn'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): operations.validate_rendered(self.ctx, data)
        data = copy.deepcopy(self.models['rpi5']); data['services']['sn']['environment']['LD_PRELOAD'] = '/tmp/unknown.so'
        with self.assertRaises(ValueError): operations.validate_rendered(self.ctx, data)

    def test_wrong_port_rejected(self):
        data = copy.deepcopy(self.models['rpi5']); data['services']['sl-a']['ports'][0]['published'] = '19002'
        with self.assertRaises(ValueError): operations.validate_rendered(self.ctx, data)

    def test_wrong_compose_labels_rejected(self):
        data = copy.deepcopy(self.models['rpi5']); data['services']['sn']['labels']['io.openswarm.rebuild.owner'] = 'foreign'
        with self.assertRaises(ValueError): operations.validate_rendered(self.ctx, data)

    def test_wrong_mount_or_automatic_bind_creation_rejected(self):
        for key, value in [('read_only', False), ('bind', {'create_host_path': True})]:
            data = copy.deepcopy(self.models['rpi5']); data['services']['sl-a']['volumes'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): operations.validate_rendered(self.ctx, data)

    def test_no_cross_host_depends_on(self):
        for data in self.models.values():
            for service in data['services'].values(): self.assertTrue(set(service.get('depends_on', {})) <= set(data['services']))

    def test_missing_or_invalid_host_selection(self):
        with self.assertRaises(ValueError): contract.load_context('invalid', self.ctx.env_path)
        with self.assertRaises(ValueError): contract.load_context('opi5-pro', self.ctx.env_path)

    def test_env_examples_refuse_missing_operator_inputs(self):
        for host, name in [('rpi5', 'env.host-a.example'), ('opi5-pro', 'env.host-b.example')]:
            with self.subTest(host=host), self.assertRaises(ValueError): contract.load_context(host, ROOT / name)

    def test_missing_selected_artifact_rejected(self):
        target = self.ctx.root / 'deployment/config/sl-a.json'
        old = target.read_bytes(); target.chmod(0o644)
        try:
            target.unlink()
            with self.assertRaises((ValueError, OSError)): contract.load_context('rpi5', self.ctx.env_path)
        finally:
            target.write_bytes(old); target.chmod(0o444)

    def test_env_mismatches_rejected(self):
        for key, value in [('RUN_ID', 'wrong'), ('BUNDLE_ID', 'sha256:' + '0' * 64), ('HOST_A_IP', '8.8.8.8'), ('SL_A_HOST_PORT', '19002')]:
            path = self.base / ('bad-' + key + '.env'); values = {**self.ctx.env, key: value}
            path.write_text(''.join(k + '=' + v + '\n' for k, v in values.items()), encoding='utf-8')
            with self.subTest(key=key), self.assertRaises(ValueError): contract.load_context('rpi5', path)

    def test_no_overwrite_env_or_job_tree(self):
        before = self.ctx.env_path.read_bytes()
        with self.assertRaises(ValueError): contract.configure(self.ctx.root, 'rpi5', self.bundle_id, self.ctx.env_path)
        self.assertEqual(before, self.ctx.env_path.read_bytes())
        with self.assertRaises(ValueError): contract.configure(self.ctx.root, 'rpi5', self.bundle_id, self.ctx.root / 'new.env')

    def test_context_works_after_owned_runtime_files_exist(self):
        path = self.ctx.root / 'deployment/data/checkpoints/client-a/round-1.iutfw'
        try:
            path.write_bytes(b'fixture runtime state, not model evidence')
            contract.load_context('rpi5', self.ctx.env_path)
        finally: path.unlink()  # Chỉ dọn fixture tạm do chính test này tạo.

    def test_clean_environment_removes_compose_and_value_overrides(self):
        with patch.dict(os.environ, {'COMPOSE_PROJECT_NAME': 'old-task008', 'HOST_A_IP': '0.0.0.0'}):
            clean = contract.clean_environment(self.ctx)
            self.assertNotIn('COMPOSE_PROJECT_NAME', clean); self.assertNotIn('HOST_A_IP', clean)

    def test_preflight_never_claims_container_path_pass(self):
        report = preflight.observations(self.ctx, self.runner, True)
        statuses = {x['check']: x['status'] for x in report['checks']}
        self.assertEqual(statuses['container_network_paths'], 'REQUIRES PHYSICAL TESTING')
        self.assertEqual(report['physical_connectivity'], 'NOT VERIFIED'); self.assertFalse(report['deployment_authorized'])
        self.assertEqual(self.runner.changes, [])

    def test_missing_endpoints_not_tested_not_pass(self):
        self.runner.offline_http = True
        report = preflight.observations(self.ctx, self.runner, True)
        self.assertTrue(all(x['status'] == 'NOT TESTED' for x in report['checks'] if x['check'].startswith('host_endpoint_')))

    def test_rejected_context_never_queries_remote_engine_or_endpoints(self):
        with patch.object(preflight, 'local_engine', side_effect=ValueError('Remote context refused')):
            report = preflight.observations(self.ctx, self.runner, True)
        self.assertEqual(next(x['status'] for x in report['checks'] if x['check'] == 'local_host_engine_compose'), 'FAIL')
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.runner.changes, [])

    def test_interface_route_and_port_conflicts(self):
        self.runner.local_ip = False; self.runner.no_route = True
        self.runner.listeners = 'LISTEN 0 128 0.0.0.0:18000 0.0.0.0:*'
        report = preflight.observations(self.ctx, self.runner)
        status = {x['check']: x['status'] for x in report['checks']}
        for key in ('private_interface', 'peer_route', 'port_sn'): self.assertEqual(status[key], 'FAIL')

    def test_docker_dnat_conflict_without_ss_listener(self):
        self.runner.foreign_ports = [{'id': 'f' * 12, 'ports': '0.0.0.0:18000-18001->8000-8001/tcp'}]
        report = preflight.observations(self.ctx, self.runner)
        self.assertEqual(next(x['status'] for x in report['checks'] if x['check'] == 'port_sn'), 'FAIL')

    def test_mislabelled_actual_port_bindings_rejected(self):
        self.runner.item('sn')['ports'] = {'8000/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '18000'}]}
        with self.assertRaises(ValueError): preflight.owned(self.ctx, self.runner)

    def test_wrong_existing_container_owner_rejected_before_stop(self):
        self.runner.item('sn')['labels']['io.openswarm.rebuild.owner'] = 'old-single-host'
        with self.assertRaises(ValueError): operations.stop(self.ctx, self.runner)
        self.assertFalse(self.runner.changes)

    def test_wrong_network_owner_rejected_before_stop(self):
        self.runner.item('sn'); self.runner.network = True; self.runner.bad_network = True
        with self.assertRaises(ValueError): operations.stop(self.ctx, self.runner)
        self.assertFalse(self.runner.changes)

    def test_stop_only_owned_exact_ids_mock(self):
        for role in contract.ROLES['rpi5']: self.runner.item(role)
        result = operations.stop(self.ctx, self.runner)
        self.assertEqual(len(result['stopped_ids']), 4)
        self.assertEqual(self.runner.changes[0][-1], self.runner.items['round-controller']['id'])
        self.assertTrue(all(x[:3] == ['docker', 'container', 'stop'] for x in self.runner.changes))

    def test_execution_approval_gate(self):
        self.runner.approved = False
        with self.assertRaises(ValueError): operations.start(self.ctx, self.runner, 'sn')
        with self.assertRaises(ValueError): operations.stop(self.ctx, self.runner)
        self.assertEqual(self.runner.changes, [])

    def test_no_controller_or_sn_stage_on_host_b(self):
        for stage in ('sn', 'controller'):
            with self.subTest(stage=stage), self.assertRaises(ValueError): operations.plan_start(self.contexts['opi5-pro'], stage)

    def test_plan_uses_exact_stage_no_deps_no_recreate_no_pull(self):
        for stage in ('sn', 'sl', 'clients', 'controller'):
            role, args = operations.plan_start(self.ctx, stage)
            self.assertIn('--no-deps', args); self.assertIn('--no-recreate', args); self.assertIn('--no-build', args)
            self.assertEqual(args[args.index('--pull') + 1], 'never'); self.assertEqual(args[-1], role)

    def test_start_refuses_deferred_native_ownership(self):
        with self.assertRaises(ValueError): operations.start(self.ctx, self.runner, 'sn')
        self.assertFalse(self.runner.changes)

    def test_mock_new_sn_stage_only(self):
        with patch.object(contract, 'runtime_ownership'):
            result = operations.start(self.ctx, self.runner, 'sn')
        self.assertEqual(result['service'], 'sn'); self.assertEqual(len(self.runner.changes), 1)

    def test_existing_stopped_service_never_replayed(self):
        self.runner.item('sn', running=False)
        with patch.object(contract, 'runtime_ownership'), self.assertRaises(ValueError): operations.start(self.ctx, self.runner, 'sn')
        self.assertFalse(self.runner.changes)

    def test_clients_require_path_ack_and_fresh_shared_state(self):
        self.runner.item('sn'); self.runner.item('sl-a')
        with patch.object(contract, 'runtime_ownership'), self.assertRaises(ValueError): operations.start(self.ctx, self.runner, 'clients')
        self.assertFalse(self.runner.changes)
        with patch.object(contract, 'runtime_ownership'):
            result = operations.start(self.ctx, self.runner, 'clients', ack_paths=True)
        self.assertEqual(result['service'], 'client-a')

    def test_controller_requires_remote_client_ack(self):
        self.runner.item('sn'); self.runner.item('sl-a'); self.runner.item('client-a')
        with patch.object(contract, 'runtime_ownership'), self.assertRaises(ValueError): operations.start(self.ctx, self.runner, 'controller', ack_paths=True)
        self.assertFalse(self.runner.changes)
        with patch.object(contract, 'runtime_ownership'):
            result = operations.start(self.ctx, self.runner, 'controller', ack_paths=True, ack_remote_client=True)
        self.assertEqual(result['service'], 'round-controller')

    def test_stale_round_or_model_blocks_clients(self):
        for key in ('round_exists', 'bad_model'):
            self.runner = FakeRunner(self.ctx, self.models['rpi5']); self.runner.item('sn'); self.runner.item('sl-a'); setattr(self.runner, key, True)
            with patch.object(contract, 'runtime_ownership'), self.assertRaises(ValueError): operations.start(self.ctx, self.runner, 'clients', ack_paths=True)
            self.assertFalse(self.runner.changes)

    def test_missing_local_client_blocks_controller(self):
        self.runner.item('sn'); self.runner.item('sl-a')
        with patch.object(contract, 'runtime_ownership'), self.assertRaises(ValueError): operations.start(self.ctx, self.runner, 'controller', True, True)
        self.assertFalse(self.runner.changes)

    def test_check_cli_is_static_only(self):
        output = io.StringIO()
        with patch.object(preflight, 'Runner', return_value=self.runner), contextlib.redirect_stdout(output):
            result = operations.main(['check', '--host', 'rpi5', '--env', str(self.ctx.env_path)])
        self.assertEqual(result, 0); self.assertFalse(self.runner.changes)
        self.assertFalse(any(x[:2] == ['docker', 'info'] for x in self.runner.calls))


if __name__ == '__main__': unittest.main(verbosity=2)
