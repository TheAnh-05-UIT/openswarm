"""Kiểm thử cục bộ; các test SWOP tùy chọn dùng package đã có sẵn.

Không tạo, pull, build hoặc dừng tài nguyên Docker. Compose config có bộ kiểm tra
riêng. Lệnh chạy: python -B -m unittest discover -s tests -v
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value
init = module('initialize', ROOT / 'config/initialize.py')
verify = module('verify', ROOT / 'config/verify.py')
COMPOSE = json.loads((ROOT / 'compose.yaml').read_text(encoding='utf-8'))
TEMPLATE = json.loads((ROOT / 'config/profile.template.json').read_text(encoding='utf-8'))
ENV = dict(line.split('=', 1) for line in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#'))
ENV.update(RUN_ID='20261101t120000z-1234abcd', EXCLUSIVE_OWNER_ACK='true')
ASSET = (ROOT / 'config/assets/initial.iutfw').read_bytes()
WORK = Path(os.environ.get('OPENSWARM_TEST_WORK', ROOT / 'tests/tmp'))
WORK.mkdir(parents=True, exist_ok=True)
HAS_SWOP = importlib.util.find_spec('swop') is not None
BASH = os.environ.get('BASH_BIN') or shutil.which('bash')

class ComposeTests(unittest.TestCase):
    def test_default_topology(self):
        active = {s for s, v in COMPOSE['services'].items() if 'profiles' not in v}
        self.assertEqual(active, {'bundle-init', 'sn', 'sl-a', 'sl-b', 'client-a', 'client-b', 'round-controller'})
    def test_four_image_families(self):
        images = {s['image'] for s in COMPOSE['services'].values()}
        self.assertEqual(len(images), 4)
        for image in images:
            self.assertIn('${IMAGE_NAMESPACE:?', image); self.assertNotIn('latest', image)
    def test_only_arm64_images(self):
        for s in COMPOSE['services'].values():
            self.assertEqual(s['platform'], 'linux/arm64'); self.assertNotIn('build', s)
            self.assertEqual(s['logging'], {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}})
    def test_no_published_ports(self):
        for s in COMPOSE['services'].values():
            self.assertNotIn('ports', s); self.assertNotIn('privileged', s)
    def test_no_socket_or_external_path(self):
        for s in COMPOSE['services'].values():
            for v in s.get('volumes', []):
                self.assertNotIn('docker.sock', v['source']); self.assertNotIn('..', v['source'])
                if v['type'] == 'bind':
                    self.assertEqual(v['source'], './config'); self.assertTrue(v['read_only'])
    def test_internal_dns(self):
        self.assertTrue(COMPOSE['networks']['swarm']['internal'])
        for s in ('sn', 'sl-a', 'sl-b'):
            self.assertEqual(COMPOSE['services'][s]['networks']['swarm']['aliases'], [s + '.openswarm.internal'])
    def test_controller_namespace_and_single_writer(self):
        writers = [s for s in COMPOSE['services'].values() if 'orchestrate' in s.get('command', [])]
        self.assertEqual(len(writers), 1)
        self.assertEqual(writers[0]['network_mode'], 'service:sn')
        self.assertIn('--exclusive-owner', writers[0]['command'])
        self.assertIn('/controller-data/rounds.jsonl', writers[0]['command'])
    def test_jobs_not_help_or_auto_restart(self):
        for name, s in COMPOSE['services'].items():
            self.assertEqual(s['restart'], 'no'); self.assertNotIn('--help', s['command'])
            if name.startswith('client-'):
                self.assertTrue(s['entrypoint'][1].endswith('/runtime/user_entry.py'))
                self.assertEqual(s['command'], []); self.assertEqual(s['environment']['TF_NUM_INTRAOP_THREADS'], '1')
    def test_nonroot_daemons(self):
        for name, s in COMPOSE['services'].items():
            self.assertEqual(s['user'], '0:0' if name == 'bundle-init' else '65532:65532')
            self.assertEqual(s['cap_drop'], ['ALL']); self.assertTrue(s['read_only'])
        self.assertEqual(COMPOSE['services']['bundle-init']['cap_add'], ['CHOWN', 'SETUID', 'SETGID'])
        self.assertEqual(COMPOSE['services']['bundle-init']['network_mode'], 'none')
    def test_direct_scripts_can_import_image_packages(self):
        # Khi chạy file trực tiếp, Python đưa /config vào sys.path[0]; cần PYTHONPATH=/app.
        for name in ('bundle-init', 'verify', 'verify-fedavg', 'client-a', 'client-b', 'sl-a', 'sl-b'):
            self.assertEqual(COMPOSE['services'][name]['environment']['PYTHONPATH'], '/app')
    def test_tensorflow_cache_is_writable_tmpfs(self):
        for name in ('client-a', 'client-b', 'verify-fedavg'):
            env = COMPOSE['services'][name]['environment']
            self.assertEqual(env['HOME'], '/tmp')
            self.assertEqual(env['KERAS_HOME'], '/tmp/.keras')
    def test_init_is_dependency(self):
        for s in ('sn', 'sl-a', 'sl-b', 'client-a', 'client-b', 'round-controller'):
            self.assertEqual(COMPOSE['services'][s]['depends_on']['bundle-init']['condition'], 'service_completed_successfully')
    def test_health_and_client_dependencies(self):
        self.assertIn('/health', str(COMPOSE['services']['sn']['healthcheck']))
        for suffix in ('a', 'b'):
            self.assertIn('/v1/ml/session', str(COMPOSE['services']['sl-' + suffix]['healthcheck']))
            self.assertEqual(COMPOSE['services']['client-' + suffix]['depends_on']['sl-' + suffix]['condition'], 'service_healthy')
    def test_persistence(self):
        self.assertEqual(set(COMPOSE['volumes']), {'deployment', 'checkpoint-a', 'checkpoint-b', 'controller-data'})
        for suffix in ('a', 'b'):
            mounts = COMPOSE['services']['client-' + suffix]['volumes']
            self.assertIn({'type': 'volume', 'source': 'checkpoint-' + suffix, 'target': '/tmp/checkpoints', 'read_only': False}, mounts)
    def test_active_ram_caps(self):
        active = [s for name, s in COMPOSE['services'].items() if name != 'bundle-init' and 'profiles' not in s]
        self.assertEqual(sum(int(s['mem_limit'][:-1]) for s in active), 5760)
        self.assertTrue(all(s['mem_limit'] == '2048m' for name, s in COMPOSE['services'].items() if name.startswith('client-')))
    def test_env_exactly_used(self):
        variables = set(re.findall(r'\$\{([A-Z][A-Z0-9_]*)', (ROOT / 'compose.yaml').read_text(encoding='utf-8')))
        self.assertEqual(variables, set(ENV))
    def test_required_placeholders(self):
        self.assertEqual(dict(line.split('=', 1) for line in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines() if '=' in line and not line.startswith('#'))['RUN_ID'], '')
        self.assertNotIn('secrets', COMPOSE)
    def test_secrets_ignored_bootstrap_tracked(self):
        ignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('secrets/*', ignore); self.assertIn('!secrets/.gitkeep', ignore)
        self.assertNotIn('*.iutfw', ignore)
    def test_no_monitoring(self):
        self.assertNotIn('monitoring', COMPOSE['services'])
        self.assertEqual(TEMPLATE['monitoring'], {'enabled': False})

class ProfileTests(unittest.TestCase):
    def test_bootstrap_identity(self):
        self.assertEqual(hashlib.sha256(ASSET).hexdigest(), init.ASSET_SHA)
    def test_valid_profile(self):
        p = init.make_profile(TEMPLATE, ENV)
        self.assertEqual(p['job_id'], 'pi-' + ENV['RUN_ID'])
        self.assertEqual(p['orchestration']['host'], 'sn-target')
        self.assertEqual([s['params']['round_ids'] for s in p['services'] if s['role'] == 'user_tf'], [[1, 2], [1, 2]])
    def test_different_samples_and_data(self):
        clients = [s for s in init.make_profile(TEMPLATE, ENV)['services'] if s['role'] == 'user_tf']
        self.assertNotEqual(clients[0]['params']['samples'], clients[1]['params']['samples'])
        self.assertNotEqual(clients[0]['params']['data_seed'], clients[1]['params']['data_seed'])
    def test_profile_unchanged(self):
        before = copy.deepcopy(TEMPLATE); init.make_profile(TEMPLATE, ENV); self.assertEqual(before, TEMPLATE)
    def test_invalid_environment(self):
        cases = [('RUN_ID', '../existing'), ('EXCLUSIVE_OWNER_ACK', 'false'), ('IMAGE_NAMESPACE', '../bad'),
                 ('SN_VERSION', 'latest'), ('SL_PORT', '80'), ('ROUNDS', '3'), ('SAMPLES_A', '0'),
                 ('EPOCHS', '50'), ('LEARNING_RATE', '0.2'), ('STARTUP_TIMEOUT', 'nan'),
                 ('CONTROLLER_PORT', ENV['SN_PORT']), ('DATA_SEED_B', '-1')]
        for key, value in cases:
            with self.subTest(key=key):
                with self.assertRaises(ValueError): init.make_profile(TEMPLATE, {**ENV, key: value})
    def test_training_step_cap(self):
        with self.assertRaises(ValueError):
            init.make_profile(TEMPLATE, {**ENV, 'SAMPLES_A': '256', 'EPOCHS': '10', 'BATCH_SIZE': '1'})
    def test_prepare_reject_nonempty_before_chown(self):
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            Path(tmp, 'old-evidence').write_text('retain', encoding='utf-8')
            with patch.object(init.os, 'chown', create=True) as chown:
                with self.assertRaises(ValueError): init.prepare_mounts([tmp])
                chown.assert_not_called(); self.assertTrue(Path(tmp, 'old-evidence').exists())
    def test_permission_drop_sequence_mock_only(self):
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            with patch.object(init.os, 'getuid', side_effect=[0, 65532], create=True), patch.object(init.os, 'getgid', return_value=65532, create=True), patch.object(init.os, 'chmod') as chmod, patch.object(init.os, 'chown', create=True) as chown, patch.object(init.os, 'setgroups', create=True) as groups, patch.object(init.os, 'setgid', create=True) as gid, patch.object(init.os, 'setuid', create=True) as uid:
                init.prepare_mounts([tmp]); chmod.assert_called_once_with(Path(tmp), 0o700)
                chown.assert_called_once_with(Path(tmp), 65532, 65532)
                groups.assert_called_once_with([]); gid.assert_called_once_with(65532); uid.assert_called_once_with(65532)

@unittest.skipUnless(HAS_SWOP, 'SWOP package required only for optional generator contract checks')
class InstalledSwopTests(unittest.TestCase):
    def test_profile_matches_accepted_schema(self):
        from swop.profile import validate
        validate(init.make_profile(TEMPLATE, ENV))
    def test_real_bundle_includes_receipt_launchers(self):
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            files = init.generate(init.make_profile(TEMPLATE, ENV), ASSET, Path(tmp) / 'bundle')
            self.assertIn(b'report_completion', files['sl-a-target/runtime/user_entry.py'])
            self.assertIn(b'CLIENT_COMPLETION_REPORTED', files['sl-b-target/runtime/user_entry.py'])
            for suffix in ('a', 'b'):
                cfg = json.loads(files['sl-' + suffix + '-target/config/client-' + suffix + '.json'])
                self.assertEqual(cfg['controller']['url'], 'http://sn.openswarm.internal:9200')
                self.assertEqual(cfg['controller']['run_id'], ENV['RUN_ID'])
                self.assertEqual(cfg['sn_url'], 'http://sn.openswarm.internal:8000')
    def test_bundle_deterministic_same_input_path(self):
        # Cấu hình sinh ra chứa đường dẫn mount cuối cùng; đối chiếu trên cùng đích.
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            target = Path(tmp) / 'bundle'; p = init.make_profile(TEMPLATE, ENV)
            one = init.generate(p, ASSET, target)
            shutil.rmtree(target); shutil.rmtree(target.parent / 'input')  # Chỉ sửa file fixture kiểm thử, không thao tác tài nguyên Docker.
            two = init.generate(p, ASSET, target)
            self.assertEqual(one, two)
    def test_existing_output_refused(self):
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            with self.assertRaises(ValueError): init.generate(init.make_profile(TEMPLATE, ENV), ASSET, tmp)
    def test_manifest_hashes_after_config_adaptation(self):
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            files = init.generate(init.make_profile(TEMPLATE, ENV), ASSET, Path(tmp) / 'bundle')
            manifest = json.loads(files['deployment-manifest.json'])
            for entry in manifest['payload_files']:
                self.assertEqual(hashlib.sha256(files[entry['path']]).hexdigest(), entry['sha256'])

def fixture():
    profile = init.make_profile(TEMPLATE, ENV); events = []
    def add(event, **fields): events.append({'sequence': len(events), 'event': event, **fields})
    add('CONTROLLER_STARTED', run_id=ENV['RUN_ID'], job_id=profile['job_id'])
    n = struct.unpack('>I', ASSET[8:12])[0]; header = json.loads(ASSET[12:12 + n])
    base = hashlib.sha256(ASSET).hexdigest(); tensor = hashlib.sha256(ASSET[12 + n:]).hexdigest()
    for rid in (1, 2):
        add('CREATE_INTENT', round_id=rid); add('ROUND_CREATED', round_id=rid)
        sha = str(rid) * 64; global_tensor = str(rid + 2) * 64
        for peer, samples in [('sl-a', 16), ('sl-b', 32)]:
            add('CLIENT_COMPLETED', receipt={'protocol_version': 1, 'run_id': ENV['RUN_ID'], 'job_id': profile['job_id'],
                'sn_instance_id': 'fixture-epoch', 'peer_id': peer, 'model_id': header['model_identity'], 'round_id': rid,
                'samples': samples, 'base_version': rid - 1, 'global_version': rid, 'base_sha256': base,
                'global_sha256': sha, 'base_tensor_sha256': tensor, 'training_start_tensor_sha256': tensor,
                'global_tensor_sha256': global_tensor})
        add('ROUND_COMPLETED', round_id=rid, base_sha256=base, global_sha256=sha, global_version=rid,
            aggregator='sl-a' if rid == 1 else 'sl-b', clients_completed=['sl-a', 'sl-b'], client_tensor_sha256=global_tensor)
        base = sha; tensor = global_tensor
    add('RUN_COMPLETED', rounds=2)
    return events, profile

class EvidenceVerifierTests(unittest.TestCase):
    def test_two_round_fixture(self):
        events, profile = fixture(); records, receipts, _, _ = verify.inspect_journal(events, profile, ASSET)
        self.assertEqual(len(records), 2); self.assertEqual(len(receipts), 4)
    def test_missing_receipt_rejected(self):
        events, profile = fixture(); events[3]['event'] = 'REMOVED_RECEIPT'
        with self.assertRaises(ValueError): verify.inspect_journal(events, profile, ASSET)
    def test_wrong_samples_rejected(self):
        events, profile = fixture(); events[3]['receipt']['samples'] = 1
        with self.assertRaises(ValueError): verify.inspect_journal(events, profile, ASSET)
    def test_carry_over_rejected(self):
        events, profile = fixture(); events[8]['receipt']['training_start_tensor_sha256'] = '0' * 64
        with self.assertRaises(ValueError): verify.inspect_journal(events, profile, ASSET)
    def test_wrong_aggregator_rejected(self):
        events, profile = fixture(); events[5]['aggregator'] = 'sl-b'
        with self.assertRaises(ValueError): verify.inspect_journal(events, profile, ASSET)
    def test_ambiguous_run_rejected(self):
        events, profile = fixture(); events[1]['event'] = 'CREATE_UNCERTAIN_OBSERVED'
        with self.assertRaises(ValueError): verify.inspect_journal(events, profile, ASSET)
    def test_wrong_run_rejected(self):
        events, profile = fixture(); events[0]['run_id'] = 'another-run'
        with self.assertRaises(ValueError): verify.inspect_journal(events, profile, ASSET)

@unittest.skipUnless(BASH, 'Bash not available')
class ShellTests(unittest.TestCase):
    def test_syntax(self):
        for script in (ROOT / 'scripts').glob('*.sh'):
            result = subprocess.run([BASH, '-n', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
    def test_literal_env_and_rejections(self):
        cases = [('valid', {}, True), ('missing-run', {'RUN_ID': ''}, False),
                 ('missing-owner', {'EXCLUSIVE_OWNER_ACK': ''}, False), ('false-owner', {'EXCLUSIVE_OWNER_ACK': 'false'}, False),
                 ('latest', {'SWOP_VERSION': 'latest'}, False), ('same-port', {'CONTROLLER_PORT': '8000'}, False),
                 ('source-injection', {'RUN_ID': '$(touch SHOULD_NOT_EXIST)'}, False),
                 ('bad-learning-rate', {'LEARNING_RATE': '0.2'}, False)]
        for name, change, allowed in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(dir=WORK) as tmp:
                for directory in ('scripts', 'config'):
                    shutil.copytree(ROOT / directory, Path(tmp) / directory)
                Path(tmp, '.env').write_text(''.join(k + '=' + v + '\n' for k, v in {**ENV, **change}.items()), encoding='utf-8')
                result = subprocess.run([BASH, '-c', 'source "$1/scripts/common.sh"; load_settings; printf "%s" "$PROJECT"', '_', Path(tmp).as_posix()], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                self.assertFalse(Path(tmp, 'SHOULD_NOT_EXIST').exists())

    def scenario(self, script, mode):
        with tempfile.TemporaryDirectory(dir=WORK) as tmp:
            root = Path(tmp)
            for directory in ('scripts', 'config'):
                shutil.copytree(ROOT / directory, root / directory)
            shutil.copy2(ROOT / 'compose.yaml', root / 'compose.yaml')
            (root / '.env').write_text(''.join(k + '=' + v + '\n' for k, v in ENV.items()), encoding='utf-8')
            fake = root / 'fake-bin'; fake.mkdir()
            (fake / 'uname').write_text('#!/usr/bin/env bash\nprintf "aarch64\\n"\n', encoding='utf-8')
            (fake / 'docker').write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$MOCK_LOG"
case "$1" in
  compose) exit 0 ;;
  context) printf 'unix:///var/run/docker.sock\\n'; exit 0 ;;
  info) printf 'linux/aarch64\\n'; exit 0 ;;
  container)
    if [[ "$2" == ls && "$MODE" == occupied ]]; then printf 'old-id\\n'; fi
    if [[ "$2" == ls && "$MODE" == stop-* ]]; then printf 'owned-id\\n'; fi
    exit 0 ;;
  network|volume)
    if [[ "$2" == inspect ]]; then
      [[ "$MODE" == named-volume && "$1" == volume ]] && exit 0
      exit 1
    fi
    exit 0 ;;
  inspect)
    if [[ "$*" == *io.openswarm.deployment.owner* ]]; then
      if [[ "$MODE" == stop-foreign ]]; then printf 'wrong|other|wrong\\n'; else printf 'openswarm-20261101t120000z-1234abcd|openswarm-deployment|20261101t120000z-1234abcd\\n'; fi
    elif [[ "$*" == *com.docker.compose.service* ]]; then
      if [[ "$MODE" == stop-numeric ]]; then printf 'verify-fedavg\\n'; else printf 'round-controller\\n'; fi
    else printf 'true\\n'; fi
    exit 0 ;;
esac
exit 96
''', encoding='utf-8')
            for path in fake.iterdir(): path.chmod(0o755)
            log = root / 'docker-calls.log'
            # Mọi lệnh ở đây đi vào Docker giả, không gọi Engine thật.
            code = 'root=$(cd "$1" && pwd); export PATH="$root/fake-bin:$PATH"; export MOCK_LOG="$root/docker-calls.log"; export MODE="$2"; unset DOCKER_HOST; hash -r; bash "$root/scripts/$3"'
            proc = subprocess.run([BASH, '-c', code, '_', root.as_posix(), mode, script], capture_output=True, text=True)
            return proc, log.read_text(encoding='utf-8') if log.exists() else ''

    def test_start_new_project_mock(self):
        proc, calls = self.scenario('start.sh', 'fresh')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(' pull', calls); self.assertIn(' up --detach --no-build --pull never --no-recreate', calls)
        self.assertIn('container ls --all', calls)
    def test_start_refuses_existing_stopped_containers_mock(self):
        proc, calls = self.scenario('start.sh', 'occupied')
        self.assertNotEqual(proc.returncode, 0); self.assertNotIn(' pull', calls); self.assertNotIn(' up ', calls)
    def test_start_refuses_unlabelled_named_volume_mock(self):
        proc, calls = self.scenario('start.sh', 'named-volume')
        self.assertNotEqual(proc.returncode, 0); self.assertNotIn(' pull', calls)
    def test_stop_refuses_foreign_ownership_mock(self):
        proc, calls = self.scenario('stop.sh', 'stop-foreign')
        self.assertNotEqual(proc.returncode, 0); self.assertNotIn('container stop', calls)
    def test_stop_only_exact_owned_id_mock(self):
        proc, calls = self.scenario('stop.sh', 'stop-owned')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(calls.count('container stop --timeout 30 owned-id'), 1)
        for forbidden in (' prune', ' rm ', ' down', ' kill'):
            self.assertNotIn(forbidden, calls)
    def test_stop_includes_owned_numeric_verifier_mock(self):
        proc, calls = self.scenario('stop.sh', 'stop-numeric')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(calls.count('container stop --timeout 30 owned-id'), 1)
    def test_logs_do_not_mutate_mock(self):
        proc, calls = self.scenario('logs.sh', 'fresh')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('logs --follow', calls)
        self.assertNotIn(' up ', calls); self.assertNotIn(' stop ', calls)
    def test_check_is_readonly_mock(self):
        proc, calls = self.scenario('check.sh', 'fresh')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('config --quiet', calls)
        self.assertNotIn(' pull', calls); self.assertNotIn(' up ', calls)

if __name__ == '__main__':
    unittest.main(verbosity=2)
