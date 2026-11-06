"""CLI quản lý từng host: check/configure/plan cục bộ, preflight chỉ đọc.

start/stop cần cờ xác nhận thực thi của operator. Công cụ host chỉ dùng thư viện
chuẩn Python; SWOP, TensorFlow và các ứng dụng chạy trong Docker image.
"""
import argparse
from datetime import datetime
import json
import os
import re
import subprocess
import time
import runtime_contract as contract
import preflight
import tooling as bundle

LOGGING_POLICY = {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}


def valid_since(value):
    """Chỉ nhận timestamp UTC tuyệt đối RFC3339; không nhận shell/duration tùy ý."""
    if value is None:
        return None
    bundle.require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z', value) is not None,
                   'since must be an absolute UTC RFC3339 timestamp ending in Z')
    try:
        datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S')
    except ValueError as exc:
        raise bundle.BundleError('Invalid since calendar/time') from exc
    return value


def log_snapshot(ctx, runner, existing, tail, since):
    """Trả prefix + metadata nếu bị cắt; không coi snapshot là cursor exactly-once."""
    command = contract.compose_args(ctx, 'logs', '--no-color', '--timestamps', '--tail', str(tail))
    if since is not None:
        command.extend(['--since', since])
    # Chỉ dùng roster đã vượt qua toàn bộ ownership checks; không mở rộng project.
    command.extend(sorted(existing))
    result = runner.capture(command, ctx, timeout=30) if existing else preflight.Capture('', '', 0, 0, 0, preflight.DEFAULT_BUDGET)
    code = 3 if not result.complete else 2 if result.returncode else 0
    return {'host': ctx.host, 'logs': result.stdout, 'stderr': result.stderr,
            'status': 'INCOMPLETE' if code == 3 else 'FAIL' if code else 'PASS',
            'timestamps': True, 'since': since, 'capture': result.metadata(),
            'boundary_overlap_possible': True}, code


def validate_rendered(ctx, data):
    """Đối chiếu Compose đã render với image/launcher/mount của bundle tin cậy."""
    services = data['services']
    bundle.require(data['name'] == ctx.project and set(services) == set(contract.ROLES[ctx.host]), 'Compose project/placement mismatch')
    base_labels = {'io.openswarm.rebuild.project': 'osm009', 'io.openswarm.rebuild.owner': 'openswarm-multihost',
                   'io.openswarm.rebuild.run': ctx.env['RUN_ID'], 'io.openswarm.rebuild.host': ctx.host,
                   'io.openswarm.bundle': ctx.env['BUNDLE_ID']}
    norm = lambda value: os.path.normcase(os.path.normpath(value))
    for role, service in services.items():
        original = ctx.upstream['services'][role]
        image = ctx.env['SN_IMAGE'] if role == 'sn' else ctx.env['SL_IMAGE'] if role.startswith('sl-') else ctx.env['USER_TF_IMAGE'] if role.startswith('client-') else ctx.env['SWOP_IMAGE']
        bundle.require(service['image'] == image and service['platform'] == 'linux/arm64'
                       and service['entrypoint'] == original['entrypoint'] and service.get('command', []) == original['command'], 'Image/entrypoint/CMD differs from SWOP runtime contract')
        bundle.require(service.get('logging') == LOGGING_POLICY, 'Explicit bounded Docker logging policy required')
        bundle.require(service['user'] == '65532:65532' and service['read_only'] is True and service['restart'] == 'no'
                       and not service.get('privileged', False) and 'ALL' in service['cap_drop']
                       and 'no-new-privileges:true' in service['security_opt'] and 'network_mode' not in service, 'Unsafe runtime security/network mode')
        bundle.require(not service.get('cap_add') and not service.get('devices')
                       and service.get('pid') != 'host' and service.get('ipc') in (None, 'private')
                       and service['security_opt'] == ['no-new-privileges:true']
                       and set(service['networks']) == {'lab'}, 'Unexpected privileges/host namespaces/extra networks')
        allowed_environment = set(original.get('environment', {})) | {'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED', 'HOME', 'HTTP_PORT', 'KERAS_HOME', 'TF_ENABLE_ONEDNN_OPTS'}
        bundle.require(set(service['environment']) <= allowed_environment, 'Unexpected environment injection')
        bundle.require(all(service['labels'].get(k) == v for k, v in {**base_labels, 'io.openswarm.rebuild.role': role}.items()), 'Wrong Compose ownership labels')
        bundle.require(set(service.get('depends_on', {})) <= set(services), 'Cross-host depends_on is invalid')
        expected_profiles = ['controller'] if role == 'round-controller' else ['training'] if role.startswith('client-') else []
        bundle.require(service.get('profiles', []) == expected_profiles, 'Unsafe automatic training/controller startup')
        bundle.require(abs(float(service['cpus']) - float(original['cpus'])) < 1e-9
                       and int(service['mem_limit']) == int(original['mem_limit'][:-1]) * 1024 * 1024, 'Resource caps differ from canonical profile')
        expected_ports = [(p['host_ip'], int(p['published']), int(p['target']), p['protocol']) for p in original.get('ports', [])]
        actual_ports = [(p['host_ip'], int(p['published']), int(p['target']), p['protocol']) for p in service.get('ports', [])]
        bundle.require(actual_ports == expected_ports, 'Unsafe public bind or canonical port mismatch')
        for address, *_ in actual_ports: bundle.private_ipv4(address)
        expected_mounts = {(norm(str(ctx.root / 'deployment' / m['source'].removeprefix('./'))), m['target'], m.get('read_only', False)) for m in original.get('volumes', [])}
        actual_mounts = set()
        for m in service.get('volumes', []):
            bundle.require(m['type'] == 'bind' and m.get('bind', {}).get('create_host_path') is False
                           and 'docker.sock' not in m['source'], 'Unsafe/auto-created mount')
            actual_mounts.add((norm(m['source']), m['target'], m.get('read_only', False)))
        bundle.require(actual_mounts == expected_mounts, 'Mounts differ from exact TASK-009B host artifacts')
        if role != 'round-controller':
            for key, value in original['environment'].items():
                bundle.require(str(service['environment'].get(key)) == str(value), 'Required launcher environment mismatch')
        if role.startswith('client-'):
            bundle.require(service['environment']['KERAS_HOME'] == '/tmp/.keras', 'Keras writable cache missing')
    network = data['networks']['lab']
    bundle.require(set(data['networks']) == {'lab'} and network['driver'] == 'bridge'
                   and network.get('internal', False) is False and not data.get('volumes')
                   and not data.get('secrets'), 'Use independent egress-capable bridge and local bind storage')
    bundle.require(all(network['labels'].get(k) == v for k, v in {**base_labels, 'io.openswarm.rebuild.role': 'network-lab'}.items()), 'Network labels mismatch')
    return data


def render(ctx, runner):
    """Chỉ parse Compose config, không kết nối Engine để tạo tài nguyên."""
    command = contract.compose_args(ctx, '--profile', 'training', '--profile', 'controller', 'config', '--format', 'json')
    return validate_rendered(ctx, json.loads(runner.read(command, ctx)))


def plan_start(ctx, stage):
    """Lập một lệnh cho đúng service/host; không tự gọi hoặc tạo dependencies."""
    roles = {'sn': 'sn', 'sl': 'sl-a' if ctx.host == 'rpi5' else 'sl-b',
             'clients': 'client-a' if ctx.host == 'rpi5' else 'client-b', 'controller': 'round-controller'}
    bundle.require(stage in roles and roles[stage] in contract.ROLES[ctx.host], 'Stage is not assigned to this host')
    return roles[stage], contract.compose_args(ctx, 'up', '--detach', '--no-build', '--pull', 'never', '--no-recreate', '--no-deps', roles[stage])


def image_prerequisite(ctx, runner, image):
    raw = runner.read(['docker', 'image', 'inspect', '--format', '{"os":{{json .Os}},"architecture":{{json .Architecture}},"user":{{json .Config.User}},"id":{{json .Id}}}', image], ctx)
    value = json.loads(raw)
    bundle.require(value['os'] == 'linux' and value['architecture'] == 'arm64' and value['user'] == '65532:65532', 'Existing local ARM64 non-root image required; no automatic pull')
    return value


def start(ctx, runner, stage, ack_paths=False, ack_remote_client=False):
    """Chạy một stage đã được operator cho phép, từ chối replay dữ liệu cũ."""
    role, command = plan_start(ctx, stage)
    bundle.require(runner.approved, 'NOT EXECUTED: --execute-approved is required after operator approval')
    rendered = render(ctx, runner)
    contract.runtime_ownership(ctx)
    report = preflight.observations(ctx, runner)
    required = {'local_host_engine_compose', 'resource_ownership', 'private_interface', 'peer_route'}
    observed = {x['check']: x for x in report['checks']}
    bundle.require(all(observed.get(k, {}).get('status') == 'PASS' for k in required), 'Local host/Engine/interface/route readiness failed or not tested')
    if not role.startswith('client-'):
        bundle.require(observed.get('port_' + role, {}).get('status') == 'PASS', 'Local published port conflict/not tested')
    existing, network = preflight.owned(ctx, runner)
    bundle.require(role not in existing, 'Service container already exists, even if stopped; no job replay/recreate')
    if stage == 'sn':
        bundle.require(not existing and not network, 'SN requires a completely new project/network')
        bundle.require(all(not any((ctx.root / p).iterdir()) for p in contract.RUNTIME[ctx.host]), 'Fresh empty local storage required')
    else:
        if ctx.host == 'rpi5':
            bundle.require('sn' in existing and existing['sn']['state'].get('Health', {}).get('Status') == 'healthy', 'Host A requires its own healthy SN container')
        preflight.fresh_shared_state(ctx, runner, both_sl=stage in ('clients', 'controller'))
        local_sl = 'sl-a' if ctx.host == 'rpi5' else 'sl-b'
        if stage in ('clients', 'controller'):
            bundle.require(local_sl in existing and existing[local_sl]['state'].get('Health', {}).get('Status') == 'healthy', 'Local SL must already be healthy')
            bundle.require(ack_paths, 'Operator must acknowledge approved container-path probes; this flag is not measured network proof')
        if stage == 'clients':
            checkpoint = ctx.root / contract.RUNTIME[ctx.host][0]
            bundle.require(not any(checkpoint.iterdir()), 'Existing checkpoints forbid automatic client rerun')
        if stage == 'controller':
            bundle.require(ack_remote_client, 'No remote-client readiness API exists: explicit operator confirmation required')
            bundle.require('client-a' in existing and existing['client-a']['state'].get('Running'), 'Local Client-A must be running')
            bundle.require(not any((ctx.root / 'deployment/swop-output').iterdir()), 'A fresh controller journal directory is required')
    image_prerequisite(ctx, runner, rendered['services'][role]['image'])
    output = runner.change(command, ctx)
    if stage == 'sn':
        deadline = time.monotonic() + 30
        while True:
            try:
                preflight.fresh_shared_state(ctx, runner, both_sl=False)
                break
            except (OSError, ValueError):
                bundle.require(time.monotonic() < deadline, 'SN readiness timeout after create; inspect retained resources; no retry/cleanup')
                time.sleep(.25)
    return {'status': 'START_REQUESTED', 'host': ctx.host, 'service': role, 'project': ctx.project,
            'output': output, 'callback_path': 'NOT PROVEN before controller listener exists; verify in approved runtime task',
            'physical_multihost': 'NOT VERIFIED', 'automatic_retry': False}


def stop(ctx, runner):
    """Kiểm tra toàn bộ ownership rồi dừng exact IDs; không xóa dữ liệu."""
    bundle.require(runner.approved, 'NOT EXECUTED: --execute-approved is required after operator approval')
    preflight.local_engine(ctx, runner)
    existing, _ = preflight.owned(ctx, runner)  # Kiểm tra quyền sở hữu toàn bộ danh sách trước khi dừng bất kỳ ID nào.
    stopped = []
    for role in ('round-controller', 'client-a', 'client-b', 'sl-a', 'sl-b', 'sn'):
        if role in existing and existing[role]['state'].get('Running'):
            identifier = existing[role]['id']
            runner.change(['docker', 'container', 'stop', '--timeout', '30', identifier], ctx)
            stopped.append(identifier)
    return {'status': 'STOP_REQUESTED', 'stopped_ids': stopped, 'resources_deleted': [], 'project': ctx.project}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('configure'); c.add_argument('--host', choices=bundle.HOSTS, required=True)
    c.add_argument('--job-root', required=True); c.add_argument('--expected-id', required=True); c.add_argument('--output', required=True)
    for name in ('check', 'preflight', 'plan', 'start', 'status', 'logs', 'stop'):
        c = sub.add_parser(name); c.add_argument('--host', choices=bundle.HOSTS, required=True); c.add_argument('--env', required=True)
        if name in ('plan', 'start'): c.add_argument('--stage', choices=('sn', 'sl', 'clients', 'controller'), required=True)
        if name in ('start', 'stop'): c.add_argument('--execute-approved', action='store_true')
        if name == 'start':
            c.add_argument('--ack-container-paths', action='store_true'); c.add_argument('--ack-remote-client', action='store_true')
        if name == 'preflight': c.add_argument('--probe-endpoints', action='store_true')
        if name == 'logs':
            c.add_argument('--tail', type=int, default=100)
            c.add_argument('--since', help='UTC RFC3339 timestamp, ví dụ 2026-11-05T10:00:00Z')
    args = parser.parse_args(argv)
    try:
        exit_code = 0
        if args.command == 'logs':
            bundle.require(1 <= args.tail <= 1000, 'Logs tail must be 1..1000')
            args.since = valid_since(args.since)
        if args.command == 'configure':
            result = {'status': 'CONFIGURED_OFFLINE', 'settings': contract.configure(args.job_root, args.host, args.expected_id, args.output)}
        else:
            ctx = contract.load_context(args.host, args.env)
            runner = preflight.Runner(approved=getattr(args, 'execute_approved', False))
            if args.command == 'check':
                render(ctx, runner); result = {'status': 'PASS', 'scope': 'Pinned import + static Compose only', 'host': ctx.host, 'physical_connectivity': 'NOT VERIFIED'}
            elif args.command == 'preflight':
                result = preflight.observations(ctx, runner, args.probe_endpoints)
                exit_code = preflight.readiness(result, ctx.host, args.probe_endpoints)
            elif args.command == 'plan':
                role, command = plan_start(ctx, args.stage); result = {'status': 'NOT EXECUTED', 'service': role, 'command': command, 'requires_operator_approval': True}
            elif args.command == 'start': result = start(ctx, runner, args.stage, args.ack_container_paths, args.ack_remote_client)
            elif args.command == 'stop': result = stop(ctx, runner)
            else:
                preflight.local_engine(ctx, runner); existing, _ = preflight.owned(ctx, runner)
                if args.command == 'status':
                    result = {'host': ctx.host, 'project': ctx.project, 'services': {k: {'id': v['id'], 'status': v['state'].get('Status'), 'exit_code': v['state'].get('ExitCode'), 'health': v['state'].get('Health', {}).get('Status')} for k, v in existing.items()}}
                else:
                    result, exit_code = log_snapshot(ctx, runner, existing, args.tail, args.since)
        print(json.dumps(result, indent=2, allow_nan=False)); return exit_code
    except preflight.CaptureError as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc), 'capture': exc.capture,
                          'automatic_retry_cleanup': False})); return 2
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)[:512], 'automatic_retry_cleanup': False})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
