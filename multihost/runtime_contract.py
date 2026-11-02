"""Đọc host import đã ghim bundle ID và được SWOP kiểm tra trước đó.

Chỉ dùng thư viện chuẩn trên host; không sinh job thay thế hoặc cài SWOP lên board.
Bước prepare/import trong tooling.py chịu trách nhiệm kiểm tra đầy đủ bằng SWOP.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tooling as bundle

ROOT = Path(__file__).resolve().parent
ROLES = {'rpi5': ('sn', 'sl-a', 'client-a', 'round-controller'), 'opi5-pro': ('sl-b', 'client-b')}
RUNTIME = {'rpi5': ('deployment/data/checkpoints/client-a', 'deployment/swop-output'),
           'opi5-pro': ('deployment/data/checkpoints/client-b',)}


@dataclass
class Context:
    host: str
    root: Path
    env_path: Path
    env: dict
    profile: dict
    manifest: dict
    receipt: dict
    upstream: dict

    @property
    def project(self):
        return 'osm009-' + self.env['RUN_ID'] + '-' + self.host

    @property
    def compose(self):
        return ROOT / ('compose.host-a.yaml' if self.host == 'rpi5' else 'compose.host-b.yaml')


def expected_env(profile, root, host, bundle_id):
    """Suy ra env từ profile đã duyệt; không cho env ghi đè cấu hình canonical."""
    services = {s['id']: s for s in profile['services']}
    addresses = {h['id']: h['address'] for h in profile['hosts']}
    out = {'HOST_ID': host, 'RUN_ID': profile['deployment']['run_id'], 'BUNDLE_ID': bundle_id,
           'JOB_ROOT': root.as_posix(), 'HOST_A_IP': addresses['rpi5'], 'HOST_B_IP': addresses['opi5-pro'],
           'SN_IMAGE': services['sn']['image'], 'SL_IMAGE': services['sl-a']['image'],
           'USER_TF_IMAGE': services['client-a']['image'], 'SWOP_IMAGE': profile['deployment']['swop_image'],
           'SN_PORT': str(services['sn']['port']), 'SN_HOST_PORT': str(services['sn']['published_port']),
           'SL_A_PORT': str(services['sl-a']['port']), 'SL_A_HOST_PORT': str(services['sl-a']['published_port']),
           'SL_B_PORT': str(services['sl-b']['port']), 'SL_B_HOST_PORT': str(services['sl-b']['published_port']),
           'CONTROLLER_PORT': str(profile['orchestration']['port']),
           'CONTROLLER_HOST_PORT': str(profile['orchestration']['published_port']),
           'PEER_TTL': str(services['sn']['params'].get('peer_ttl', 120))}
    for key, sid in [('SN', 'sn'), ('SL_A', 'sl-a'), ('SL_B', 'sl-b'), ('CLIENT_A', 'client-a'), ('CLIENT_B', 'client-b')]:
        role = services[sid]['role']; values = services[sid].get('resources', {})
        out[key + '_CPUS'] = str(values.get('cpus', 2))
        out[key + '_MEMORY'] = str(values.get('memory_mib', 2048 if role == 'user_tf' else 512)) + 'm'
    values = profile['orchestration']['resources']
    out['CONTROLLER_CPUS'] = str(values['cpus']); out['CONTROLLER_MEMORY'] = str(values['memory_mib']) + 'm'
    return out


def pinned_import(root, host, expected_id):
    """Đối chiếu file cấu hình bất biến với manifest tin cậy, tách khỏi dữ liệu runtime."""
    bundle.require(host in bundle.HOSTS, 'Explicit host must be rpi5 or opi5-pro')
    root = bundle.checked_path(root)
    metadata = bundle.strict_json(bundle.read_file(root / 'bundle.json'))
    bundle.require(isinstance(metadata, dict) and set(metadata) == {'bundle_id', 'manifest'}
                   and re.fullmatch(r'sha256:[a-f0-9]{64}', expected_id) is not None
                   and metadata['bundle_id'] == expected_id == 'sha256:' + bundle.sha(bundle.encoded(metadata['manifest'])),
                   'Pinned bundle identity mismatch')
    manifest = metadata['manifest']
    bundle.require(manifest.get('contract') == bundle.CONTRACT and manifest.get('hosts') == list(bundle.HOSTS)
                   and manifest.get('controller_host') == 'rpi5', 'Wrong bundle contract/placement')
    expected = {}
    for entry in manifest['artifacts']:
        name = bundle.relative(entry['path'])
        bundle.require(name.startswith('payload/') and type(entry['size']) is int and 0 <= entry['size'] <= bundle.MAX_FILE,
                       'Invalid artifact inventory')
        raw_name = name[len('payload/'):]; prefix = raw_name.split('/')[0]
        if prefix == host:
            local = 'deployment/' + raw_name[len(host) + 1:]
        elif prefix not in bundle.HOSTS:
            local = 'canonical/' + raw_name
        else:
            continue
        bundle.require(local not in expected, 'Duplicate local artifact')
        raw = bundle.read_file(root / local)
        bundle.require(len(raw) == entry['size'] and bundle.sha(raw) == entry['sha256'], 'Corrupt/missing artifact: ' + local)
        expected[local] = raw
    profile = bundle.strict_json(expected['canonical/profile.json'])
    bundle.require(bundle.sha(expected['canonical/profile.json']) == manifest['profile_sha256']
                   and bundle.sha(expected['canonical/assets/initial.iutfw']) == manifest['bootstrap_sha256'], 'Shared profile/bootstrap mismatch')
    bundle.require(profile['job_id'] == manifest['job_id'] == 'mh-' + manifest['run_id']
                   and profile['deployment']['run_id'] == manifest['run_id']
                   and profile['deployment']['project_prefix'] == 'osm009'
                   and profile['deployment']['owner'] == 'openswarm-multihost', 'Wrong job/project identity')
    bundle.require(re.fullmatch(r'[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}', manifest['run_id']) is not None, 'Invalid RUN_ID')
    hosts = {h['id']: h for h in profile['hosts']}
    limits = {h['host']: h for h in profile['deployment']['hosts']}
    for hid in bundle.HOSTS:
        bundle.private_ipv4(hosts[hid]['address'])
        bundle.require(hosts[hid]['address'] == limits[hid]['app_bind_ip'], 'Bind/advertised mismatch')
    bundle.require(hosts['rpi5']['address'] != hosts['opi5-pro']['address'], 'Duplicate host address')
    services = {s['id']: s for s in profile['services']}
    bundle.require(set(services) == set(bundle.PLACEMENT) and len(profile['services']) == 5, 'Invalid service roster')
    occupied = set()
    for sid, (role, hid) in bundle.PLACEMENT.items():
        s = services[sid]
        bundle.require((s['role'], s['host']) == (role, hid), 'Invalid service placement')
        if 'port' in s:
            bundle.require(type(s['port']) is int and 1024 <= s['port'] <= 65535 and
                           type(s['published_port']) is int and 1024 <= s['published_port'] <= 65535, 'Invalid application port')
            key = (hid, s['published_port']); bundle.require(key not in occupied, 'Host port conflict'); occupied.add(key)
    control = profile['orchestration']
    bundle.require(control['enabled'] is True and control['host'] == 'rpi5'
                   and control['exclusive_owner_acknowledged'] is True, 'Controller placement/owner mismatch')
    bundle.require(type(control['port']) is int and 1024 <= control['port'] <= 65535 and
                   type(control['published_port']) is int and 1024 <= control['published_port'] <= 65535 and
                   ('rpi5', control['published_port']) not in occupied, 'Controller port conflict')
    receipt = bundle.strict_json(bundle.read_file(root / 'import.json'))
    bundle.require(receipt['contract'] == bundle.CONTRACT and receipt['bundle_id'] == expected_id
                   and receipt['host'] == host and receipt['run_id'] == manifest['run_id'] and receipt['job_id'] == manifest['job_id']
                   and receipt['storage'] == bundle.storage_for(host), 'Wrong host/import identity')
    upstream = bundle.strict_json(expected['deployment/docker-compose.yaml'])
    app_roles = set(upstream['services']) - {'swop'}
    bundle.require(app_roles == set(ROLES[host]), 'Upstream service/controller mismatch')
    for sid in ROLES[host]:
        labels = upstream['services'][sid]['labels']
        bundle.require(labels['io.openswarm.rebuild.project'] == 'osm009'
                       and labels['io.openswarm.rebuild.owner'] == 'openswarm-multihost'
                       and labels['io.openswarm.rebuild.run'] == manifest['run_id']
                       and labels['io.openswarm.rebuild.host'] == host, 'Wrong upstream ownership labels')
    # Chỉ cho phép dữ liệu runtime trong thư mục thuộc job; không đọc model
    # để hiển thị trạng thái hoặc dừng job. Từ chối link và file đặc biệt.
    allowed = set(expected) | {'bundle.json', 'import.json'}
    count = 0
    def walk(directory):
        nonlocal count
        for path in directory.iterdir():
            count += 1; bundle.require(count <= 512, 'Too many local entries')
            name = bundle.relative(path.relative_to(root).as_posix()); info = path.lstat()
            bundle.require(not bundle.link_or_special(info), 'Unsafe local symlink/reparse point')
            if stat.S_ISDIR(info.st_mode):
                bundle.require(name in bundle.parent_paths(allowed) | set(RUNTIME[host]) | bundle.parent_paths(RUNTIME[host]), 'Unexpected local directory')
                walk(path)
            else:
                bundle.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Unsafe local file')
                runtime = any(name.startswith(p + '/') for p in RUNTIME[host])
                bundle.require(name in allowed or runtime, 'Unexpected local file')
    walk(root)
    for name in RUNTIME[host]: bundle.require((root / name).is_dir(), 'Required local storage missing')
    return root, profile, manifest, receipt, upstream


def parse_env(path):
    result = {}
    for line in bundle.read_file(path, 32768).decode('utf-8').splitlines():
        if not line or line.startswith('#'): continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=([A-Za-z0-9_./:\\-]*)', line)
        bundle.require(match is not None and match[1] not in result, 'Use unique literal KEY=VALUE settings; no shell/Compose interpolation')
        result[match[1]] = match[2]
    return result


def configure(root, host, expected_id, output):
    """Ghi file env mới ngoài job root; từ chối mọi file đích đã có."""
    root, profile, manifest, receipt, upstream = pinned_import(root, host, expected_id)
    values = expected_env(profile, root, host, expected_id)
    output = bundle.checked_path(output)
    bundle.require(output.parent.is_dir() and not output.exists() and not output.is_relative_to(root), 'Use a NEW env file outside immutable job storage')
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write('# Sinh từ host import đã ghim bundle ID; không chứa secret.\n')
        stream.write(''.join(k + '=' + v + '\n' for k, v in values.items()))
    return values


def load_context(host, env_path):
    """Ràng buộc host, đường dẫn, RUN_ID và bundle trước mọi thao tác vận hành."""
    bundle.require(host in bundle.HOSTS, 'Explicit host selection required')
    env_path = bundle.checked_path(env_path); values = parse_env(env_path)
    bundle.require(all(values.get(k) for k in ('HOST_ID', 'RUN_ID', 'BUNDLE_ID', 'JOB_ROOT')), 'Missing required host/run/bundle/path input')
    bundle.require(values['HOST_ID'] == host, 'Wrong explicit host selector')
    root, profile, manifest, receipt, upstream = pinned_import(values['JOB_ROOT'], host, values['BUNDLE_ID'])
    bundle.require(values == expected_env(profile, root, host, values['BUNDLE_ID']), 'Environment differs from the canonical bundle (IP/port/image/run/resource mismatch)')
    return Context(host, root, env_path, values, profile, manifest, receipt, upstream)


def runtime_ownership(ctx):
    """Chỉ chấp nhận import native đã kiểm tra UID/GID65532; không dùng staging."""
    own = ctx.receipt['ownership']
    bundle.require(os.name == 'posix' and own == {'policy': 'enforce', 'uid': 65532, 'gid': 65532, 'verified': True},
                   'Native enforced UID/GID import required; deferred staging cannot start')
    for path in [ctx.root] + [ctx.root / p for p in RUNTIME[ctx.host]]:
        bundle.require((path.stat().st_uid, path.stat().st_gid) == (65532, 65532), 'Local UID/GID mismatch')


def compose_args(ctx, *arguments):
    return ['docker', 'compose', '--project-directory', str(ROOT), '--env-file', str(ctx.env_path),
            '-p', ctx.project, '-f', str(ctx.compose), *arguments]


def clean_environment(ctx):
    result = os.environ.copy()
    for key in list(result):
        if key in ctx.env or key.startswith('COMPOSE_'): result.pop(key, None)
    return result
