"""Kiểm tra host, Engine và mạng ở chế độ chỉ đọc, có timeout; không tạo container."""
import http.client
import json
import platform
import re
import subprocess
from urllib.parse import urlsplit
import runtime_contract as contract
import tooling as bundle


class Runner:
    """Tách lệnh thật và HTTP khỏi logic kiểm tra để tests có thể dùng runner giả."""
    def __init__(self, approved=False):
        self.approved = approved

    def read(self, args, ctx, timeout=10):
        process = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                 env=contract.clean_environment(ctx), cwd=contract.ROOT)
        bundle.require(process.returncode == 0, 'Read-only command failed: ' + ' '.join(args[:3]) + ': ' + process.stderr[:300])
        bundle.require(len(process.stdout) <= 1024 * 1024, 'Command response too large')
        return process.stdout.strip()

    def change(self, args, ctx):
        bundle.require(self.approved, 'Explicit operator execution approval is required')
        allowed = args[:3] == ['docker', 'container', 'stop'] or (args[:2] == ['docker', 'compose'] and 'up' in args)
        bundle.require(allowed, 'Unsupported mutation; cleanup/pull/restart/delete is not implemented')
        process = subprocess.run(args, capture_output=True, text=True, timeout=120,
                                 env=contract.clean_environment(ctx), cwd=contract.ROOT)
        bundle.require(process.returncode == 0, 'Operation failed; state may be partial; no automatic retry/cleanup')
        return process.stdout.strip()

    def get(self, url, path, timeout=2):
        p = urlsplit(url)
        bundle.private_ipv4(p.hostname)
        bundle.require(p.scheme == 'http' and p.path in ('', '/') and p.username is None and p.password is None,
                       'Only configured private HTTP endpoints may be probed')
        connection = http.client.HTTPConnection(p.hostname, p.port, timeout=timeout)
        try:
            connection.request('GET', path, headers={'Connection': 'close'})
            response = connection.getresponse(); raw = response.read(131073)
            bundle.require(len(raw) <= 131072, 'Endpoint response too large')
            return response.status, bundle.strict_json(raw)
        finally:
            connection.close()


def local_engine(ctx, runner):
    """Yêu cầu Unix socket local và host/Engine Linux ARM64, không fallback SSH."""
    endpoint = runner.read(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], ctx)
    effective = contract.os.environ.get('DOCKER_HOST', endpoint)
    bundle.require(endpoint.startswith('unix://') and effective.startswith('unix://'), 'Only local Unix Docker contexts are allowed')
    runner.read(['docker', 'compose', 'version'], ctx)
    raw = runner.read(['docker', 'info', '--format', '{"os":{{json .OSType}},"architecture":{{json .Architecture}},"id":{{json .ID}}}'], ctx)
    info = json.loads(raw)
    bundle.require(info['os'] == 'linux' and info['architecture'] in ('aarch64', 'arm64'), 'Linux ARM64 Engine required')
    bundle.require(platform.system() == 'Linux' and platform.machine() in ('aarch64', 'arm64'), 'Local Linux ARM64 host required')
    info.update(host_system=platform.system(), host_architecture=platform.machine(), context_endpoint=endpoint)
    return info


def owned(ctx, runner):
    """Đọc labels, image, user và ports; từ chối nhắm vào tài nguyên khác job."""
    ids = runner.read(['docker', 'container', 'ls', '--all', '--no-trunc', '--quiet', '--filter',
                       'label=com.docker.compose.project=' + ctx.project], ctx).splitlines()
    result = {}
    fmt = '{"id":{{json .Id}},"labels":{{json .Config.Labels}},"state":{{json .State}},"image":{{json .Config.Image}},"user":{{json .Config.User}},"ports":{{json .HostConfig.PortBindings}}}'
    for identifier in ids:
        bundle.require(re.fullmatch(r'[a-f0-9]{12,64}', identifier) is not None, 'Unexpected container ID')
        item = json.loads(runner.read(['docker', 'container', 'inspect', '--format', fmt, identifier], ctx))
        labels = item['labels']; role = labels.get('com.docker.compose.service')
        expected = {'com.docker.compose.project': ctx.project, 'io.openswarm.rebuild.project': 'osm009',
                    'io.openswarm.rebuild.owner': 'openswarm-multihost', 'io.openswarm.rebuild.run': ctx.env['RUN_ID'],
                    'io.openswarm.rebuild.host': ctx.host, 'io.openswarm.bundle': ctx.env['BUNDLE_ID'],
                    'io.openswarm.rebuild.role': role}
        bundle.require(role in contract.ROLES[ctx.host] and all(labels.get(k) == v for k, v in expected.items()), 'Foreign/mismatched container ownership labels; refuse operation')
        bundle.require(role not in result and item['user'] == '65532:65532', 'Duplicate service/invalid runtime UID')
        ref = ctx.env['SN_IMAGE'] if role == 'sn' else ctx.env['SL_IMAGE'] if role.startswith('sl-') else ctx.env['USER_TF_IMAGE'] if role.startswith('client-') else ctx.env['SWOP_IMAGE']
        bundle.require(item['image'].removeprefix('docker.io/') == ref.removeprefix('docker.io/'), 'Owned container image mismatch')
        prefix = {'sn': 'SN', 'sl-a': 'SL_A', 'sl-b': 'SL_B', 'round-controller': 'CONTROLLER'}.get(role)
        expected_ports = {} if prefix is None else {ctx.env[prefix + '_PORT'] + '/tcp':
            [{'HostIp': ctx.env['HOST_A_IP' if ctx.host == 'rpi5' else 'HOST_B_IP'], 'HostPort': ctx.env[prefix + '_HOST_PORT']}]}
        bundle.require((item.get('ports') or {}) == expected_ports, 'Existing container published bindings mismatch')
        result[role] = item
    names = runner.read(['docker', 'network', 'ls', '--format', '{{json .Name}}'], ctx).splitlines()
    network = ctx.project + '_lab'
    exists = any(json.loads(line) == network for line in names)
    if exists:
        labels = json.loads(runner.read(['docker', 'network', 'inspect', '--format', '{{json .Labels}}', network], ctx))
        for key, value in {'io.openswarm.rebuild.project': 'osm009', 'io.openswarm.rebuild.owner': 'openswarm-multihost',
                           'io.openswarm.rebuild.run': ctx.env['RUN_ID'], 'io.openswarm.rebuild.host': ctx.host,
                           'io.openswarm.bundle': ctx.env['BUNDLE_ID'], 'io.openswarm.rebuild.role': 'network-lab'}.items():
            bundle.require(labels.get(key) == value, 'Existing network ownership mismatch')
    return result, exists


def endpoints(ctx):
    e = ctx.env
    return {'sn': 'http://' + e['HOST_A_IP'] + ':' + e['SN_HOST_PORT'],
            'sl-a': 'http://' + e['HOST_A_IP'] + ':' + e['SL_A_HOST_PORT'],
            'sl-b': 'http://' + e['HOST_B_IP'] + ':' + e['SL_B_HOST_PORT'],
            'round-controller': 'http://' + e['HOST_A_IP'] + ':' + e['CONTROLLER_HOST_PORT']}


def observations(ctx, runner, probe_endpoints=False):
    """Ghi từng check riêng; không biến host GET thành container-network PASS."""
    checks = []
    def add(name, status, detail): checks.append({'check': name, 'status': status, 'detail': detail})
    add('bundle_host_identity', 'PASS', {'host': ctx.host, 'run_id': ctx.env['RUN_ID'], 'bundle_id': ctx.env['BUNDLE_ID']})
    host_info = {'system': platform.system(), 'architecture': platform.machine()}
    add('host_architecture', 'PASS' if host_info['system'] == 'Linux' and host_info['architecture'] in ('aarch64', 'arm64') else 'FAIL', host_info)
    try:
        info = local_engine(ctx, runner); add('local_host_engine_compose', 'PASS', info)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        add('local_host_engine_compose', 'FAIL', str(exc))
        # Không truy vấn container qua context SSH/TCP đã bị từ chối.
        # Không tự kết nối board khác để thay thế một điều kiện kiểm tra bị lỗi.
        add('remaining_host_engine_network_checks', 'NOT TESTED', 'Stopped at local Engine/context prerequisite; no remote fallback')
        add('container_network_paths', 'REQUIRES PHYSICAL TESTING', 'No container-path observation performed')
        add('native_multihost_training', 'REQUIRES PHYSICAL TESTING', 'No training is executed by preflight')
        return {'host': ctx.host, 'checks': checks, 'physical_connectivity': 'NOT VERIFIED', 'deployment_authorized': False}
    own = {}; network_exists = False
    try:
        own, network_exists = owned(ctx, runner); add('resource_ownership', 'PASS', {'services': sorted(own), 'network_exists': network_exists})
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        add('resource_ownership', 'FAIL', str(exc))
    local_ip = ctx.env['HOST_A_IP' if ctx.host == 'rpi5' else 'HOST_B_IP']
    peer_ip = ctx.env['HOST_B_IP' if ctx.host == 'rpi5' else 'HOST_A_IP']
    try:
        addresses = json.loads(runner.read(['ip', '-j', '-4', 'address', 'show'], ctx))
        interfaces = [x['ifname'] for x in addresses if 'UP' in x.get('flags', []) and any(a.get('local') == local_ip for a in x.get('addr_info', []))]
        add('private_interface', 'PASS' if interfaces else 'FAIL', {'address': local_ip, 'interfaces': interfaces})
        route = json.loads(runner.read(['ip', '-j', '-4', 'route', 'get', peer_ip], ctx))
        ok = bool(route and route[0].get('dev') and route[0]['dev'] != 'lo' and route[0].get('type', 'unicast') not in ('unreachable', 'blackhole', 'prohibit'))
        add('peer_route', 'PASS' if ok else 'FAIL', route)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        add('private_interface_route', 'NOT TESTED', str(exc))
    try:
        listeners = runner.read(['ss', '-H', '-ltn'], ctx)
        occupied = set()
        for line in listeners.splitlines():
            fields = line.split()
            if len(fields) < 2: continue
            address, sep, port = fields[-2].rpartition(':')
            if sep and port.isdigit() and address in (local_ip, '0.0.0.0', '*', '[::]', '::'): occupied.add(int(port))
        # Port do Docker DNAT giữ có thể không xuất hiện như listener trong ss.
        ports = {'sn': int(ctx.env['SN_HOST_PORT']), 'sl-a': int(ctx.env['SL_A_HOST_PORT']),
                 'sl-b': int(ctx.env['SL_B_HOST_PORT']), 'round-controller': int(ctx.env['CONTROLLER_HOST_PORT'])}
        published = runner.read(['docker', 'container', 'ls', '--format', '{"id":{{json .ID}},"ports":{{json .Ports}}}'], ctx)
        foreign_ports = set()
        for line in published.splitlines():
            item = json.loads(line)
            bundle.require(re.fullmatch(r'[a-f0-9]{12,64}', item['id']) is not None, 'Unexpected published-container ID')
            own_id = any(v['id'].startswith(item['id']) for v in own.values())
            for address, first, last in re.findall(r'(\d+\.\d+\.\d+\.\d+|\[::\]):(\d+)(?:-(\d+))?->', item['ports']):
                if address in (local_ip, '0.0.0.0', '[::]'):
                    hits = {p for p in ports.values() if int(first) <= p <= int(last or first)}
                    occupied.update(hits)
                    if not own_id: foreign_ports.update(hits)
        for role in contract.ROLES[ctx.host]:
            if role.startswith('client-'): continue
            used = ports[role] in occupied
            status = 'PASS' if ports[role] not in foreign_ports and (not used or role in own and own[role]['state'].get('Running')) else 'FAIL'
            add('port_' + role, status, {'port': ports[role], 'observation': 'CONFLICT' if status == 'FAIL' else 'RESERVED BY OWNED RUN' if used else 'AVAILABLE AT OBSERVATION'})
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        add('local_port_availability', 'NOT TESTED', str(exc))
    if probe_endpoints:
        for role, url in endpoints(ctx).items():
            try:
                status, body = runner.get(url, '/v1/ml/session' if role.startswith('sl-') else '/health')
                valid = status == 200
                if role == 'sn': valid &= body.get('service') == 'independent-sn' and body.get('api_version') == '1'
                elif role.startswith('sl-'): valid &= body.get('protocol_version') == 1 and body.get('job_id') == ctx.manifest['job_id'] and body.get('peer_id') == role
                else: valid &= body.get('service') == 'swop-round-controller'
                add('host_endpoint_' + role, 'PASS' if valid else 'FAIL', {'url': url, 'http_status': status, 'vantage': 'host only'})
            except (OSError, ValueError, http.client.HTTPException) as exc:
                add('host_endpoint_' + role, 'NOT TESTED', {'url': url, 'reason': type(exc).__name__, 'note': 'May be expected before service startup; not readiness PASS'})
    else:
        add('host_endpoint_connectivity', 'NOT TESTED', 'Use --probe-endpoints for bounded read-only HTTP GET observations')
    add('container_network_paths', 'REQUIRES PHYSICAL TESTING', 'Host probes/routes do not establish container-to-published-address or cross-host container paths; run approved manual probes')
    add('native_multihost_training', 'REQUIRES PHYSICAL TESTING', 'No training is executed by preflight')
    return {'host': ctx.host, 'checks': checks, 'physical_connectivity': 'NOT VERIFIED', 'deployment_authorized': False}


def fresh_shared_state(ctx, runner, both_sl=True):
    """Xác nhận SN mới, chưa có round và SL chung epoch/bootstrap trước training."""
    urls = endpoints(ctx)
    code, health = runner.get(urls['sn'], '/health')
    bundle.require(code == 200 and health.get('service') == 'independent-sn' and health.get('api_version') == '1'
                   and health.get('storage') == 'memory' and health.get('status') == 'ok', 'SN health contract mismatch')
    epoch = health['instance_id']
    code, retained = runner.get(urls['sn'], '/v1/rounds/current')
    bundle.require(code == 404, 'A fresh SN without retained rounds is required; never reuse/resume an old job')
    if not both_sl: return epoch
    code, peers = runner.get(urls['sn'], '/v1/peers?alive_only=false')
    bundle.require(code == 200 and peers['instance_id'] == epoch and len(peers['peers']) == 2, 'Exactly two peers in the same SN epoch required')
    for peer in peers['peers']:
        role = peer['peer_id']
        bundle.require(role in ('sl-a', 'sl-b') and peer['alive'] is True and peer['endpoint'].rstrip('/') == urls[role], 'Peer identity/liveness/advertised URL mismatch')
    bundle.require({p['peer_id'] for p in peers['peers']} == {'sl-a', 'sl-b'}, 'Duplicate/missing peers')
    for role in ('sl-a', 'sl-b'):
        code, session = runner.get(urls[role], '/v1/ml/session')
        bundle.require(code == 200 and session['job_id'] == ctx.manifest['job_id'] and session['sn_instance_id'] == epoch
                       and session['model_id'] == ctx.manifest['model_id'] and session['peer_id'] == role
                       and session['participants'] == ['sl-a', 'sl-b'] and session['round'] is None
                       and session['committed']['version'] == 0
                       and session['committed']['weights_sha256'] == ctx.manifest['bootstrap_sha256'], 'Fresh SL/job/model/bootstrap mismatch')
    return epoch
