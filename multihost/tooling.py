"""Chuẩn bị bundle SWOP v2 và import phần cấu hình dành riêng cho mỗi host.

Dùng Python 3.11 và package SWOP có sẵn để kiểm tra schema, sinh cấu hình.
Không gọi Docker, SSH, mạng hay subprocess; không sửa mã ứng dụng.
Chỉ nhận bundle dạng thư mục, không giải nén ZIP/TAR.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct

HOSTS = ('rpi5', 'opi5-pro')
UID = GID = 65532
MAX_FILE = 1024 * 1024
MAX_FILES = 256
MAX_TOTAL = 8 * 1024 * 1024
RFC1918 = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
PLACEMENT = {
    'sn': ('sn', 'rpi5'), 'sl-a': ('sl', 'rpi5'), 'client-a': ('user_tf', 'rpi5'),
    'sl-b': ('sl', 'opi5-pro'), 'client-b': ('user_tf', 'opi5-pro'),
}
IMAGES = {role: 'docker.io/24520115/independent-' + repo + ':v0.1.0-arm64'
          for role, repo in [('sn', 'sn'), ('sl', 'sl'), ('user_tf', 'user-tf'), ('swop', 'swop')]}
CONTRACT = 'independent-multihost.bundle.v1'


class BundleError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BundleError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(BundleError('Non-finite JSON')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BundleError('Invalid strict JSON') from exc


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 256, 'Invalid artifact path')
    require('\\' not in value and ':' not in value and not value.startswith('/'), 'Unsafe artifact path')
    parts = value.split('/')
    for part in parts:
        require(part not in ('', '.', '..') and len(part) <= 100
                and re.fullmatch(r'[A-Za-z0-9_.-]+', part) is not None
                and not part.endswith(('.', ' ')), 'Unsafe artifact path component')
        stem = part.split('.')[0].upper()
        require(stem not in {'CON', 'PRN', 'AUX', 'NUL'}
                and re.fullmatch(r'(?:COM|LPT)[0-9]', stem) is None, 'Reserved artifact path')
    require(len(parts) <= 16 and PurePosixPath(value).as_posix() == value, 'Unsafe artifact path depth')
    return value


def link_or_special(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def checked_path(value):
    path = Path(value)
    require('..' not in path.parts, 'Filesystem traversal is unsupported')
    path = path.absolute()
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        require(not link_or_special(info), 'Symlinks/junctions/reparse points are unsupported')
    return path


def read_file(path, limit=MAX_FILE):
    path = checked_path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Only independent regular files are supported')
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Only independent regular files are supported')
        require(info.st_size <= limit, 'File exceeds size bound')
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'File exceeds size bound')
    return raw


def tree(root):
    """Duyệt có giới hạn, từ chối symlink, junction và file đặc biệt."""
    root = checked_path(root)
    require(root.is_dir(), 'A directory bundle is required; ZIP/TAR/archive inputs are unsupported')
    files, directories = {}, set()
    total = 0

    def walk(directory):
        nonlocal total
        for child in sorted(directory.iterdir()):
            name = relative(child.relative_to(root).as_posix())
            info = child.lstat()
            require(not link_or_special(info), 'Symlinks/junctions/reparse points are unsupported')
            if stat.S_ISDIR(info.st_mode):
                directories.add(name)
                require(len(directories) <= MAX_FILES, 'Too many directories')
                walk(child)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Special files/hardlinks are unsupported')
                files[name] = read_file(child)
                total += len(files[name])
                require(len(files) <= MAX_FILES and total <= MAX_TOTAL, 'Bundle exceeds size/count bounds')
    walk(root)
    return files, directories


def parent_paths(files):
    result = set()
    for name in files:
        result.update(p.as_posix() for p in PurePosixPath(name).parents if p.as_posix() != '.')
    return result


def swop_api():
    try:
        from swop.profile import Profile, load_profile, image_reference
        from swop.generate import bundle
    except ImportError as exc:
        raise BundleError('Existing SWOP v2 package is required; no fallback schema or package installation is performed') from exc
    return Profile, load_profile, image_reference, bundle


def private_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError) as exc:
        raise BundleError('A canonical RFC1918 private IPv4 address is required') from exc
    require(str(address) == value and any(address in n for n in RFC1918), 'Only RFC1918 private IPv4 addresses are supported')


def validate_profile(path):
    path = checked_path(path)
    data = strict_json(read_file(path, 256 * 1024))
    require(isinstance(data, dict) and data.get('schema_version') == 2, 'SWOP schema_version 2 is required')
    require(isinstance(data.get('bootstrap'), dict) and data['bootstrap'].get('path') == 'assets/initial.iutfw',
            'Canonical bootstrap path must be assets/initial.iutfw')
    read_file(path.parent / 'assets/initial.iutfw', 65536)
    Profile, load_profile, image_reference, _ = swop_api()
    # Dùng SWOP để kiểm tra schema, endpoint, bootstrap số học và timeout.
    profile = load_profile(path)
    data = profile.data
    run = data['deployment']['run_id']
    require(re.fullmatch(r'[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}', run) is not None, 'Missing/invalid RUN_ID')
    try:
        datetime.strptime(run[:16], '%Y%m%dt%H%M%Sz')
    except ValueError as exc:
        raise BundleError('Invalid RUN_ID calendar/time') from exc
    require(data['job_id'] == 'mh-' + run, 'job_id must equal mh-<RUN_ID>')
    require(data['deployment']['project_prefix'] == 'osm009'
            and data['deployment']['owner'] == 'openswarm-multihost', 'Dedicated multi-host ownership/project namespace required')
    hosts = {h['id']: h for h in data['hosts']}
    require(len(data['hosts']) == 2 and set(hosts) == set(HOSTS), 'Exactly rpi5 and opi5-pro hosts are required')
    settings = {h['host']: h for h in data['deployment']['hosts']}
    for hid in HOSTS:
        private_ipv4(hosts[hid]['address'])
        require(settings[hid]['app_bind_ip'] == hosts[hid]['address'], 'Bind and advertised private IPv4 must match')
        require(settings[hid]['checkpoint_root'] == './data/checkpoints'
                and settings[hid]['swop_output'] == './swop-output', 'Use reviewed host-local storage paths')
    require(hosts[HOSTS[0]]['address'] != hosts[HOSTS[1]]['address'], 'Host endpoint addresses must differ')
    services = {s['id']: s for s in data['services']}
    require(len(data['services']) == 5 and set(services) == set(PLACEMENT), 'Exactly the five reviewed application services are required')
    for sid, (role, host) in PLACEMENT.items():
        service = services[sid]
        require((service['role'], service['host']) == (role, host), 'Invalid host placement: ' + sid)
        require(image_reference(service['image']) == IMAGES[role], 'Only reviewed v0.1.0-arm64 image references are supported')
    require(image_reference(data['deployment']['swop_image']) == IMAGES['swop'], 'Reviewed SWOP image required')
    for suffix in ('a', 'b'):
        require(services['sl-' + suffix]['peer_id'] == 'sl-' + suffix, 'Expected distinct sl-a/sl-b peer IDs')
        client = services['client-' + suffix]
        require(client['sl'] == 'sl-' + suffix and client['params']['round_ids'] == [1, 2], 'Invalid client assignment/two-round contract')
    require(services['client-a']['params']['data_seed'] != services['client-b']['params']['data_seed'], 'Dataset seeds must differ')
    require(services['client-a']['params']['samples'] != services['client-b']['params']['samples'], 'Sample counts must differ')
    controller = data.get('orchestration', {})
    require(controller.get('enabled') is True and controller.get('host') == 'rpi5'
            and controller.get('exclusive_owner_acknowledged') is True, 'Exactly one acknowledged controller on rpi5 is required')
    require(data['monitoring'] == {'enabled': False}, 'Monitoring is outside this deployment contract')
    # Sắp xếp khóa chuẩn hóa để khoảng trắng/thứ tự khóa không đổi bundle ID.
    canonical = strict_json(encoded(data))
    return Profile(canonical, profile.bootstrap, profile.source)


def reference_files(profile):
    _, _, _, generate = swop_api()
    files, documents = generate(profile)
    require(set(documents) == set(HOSTS), 'Generator host inventory mismatch')
    controllers = [hid for hid, doc in documents.items() if 'round-controller' in doc['services']]
    require(controllers == ['rpi5'], 'Generator must place exactly one controller on rpi5')
    require(set(documents['rpi5']['services']) == {'sn', 'sl-a', 'client-a', 'swop', 'round-controller'}
            and set(documents['opi5-pro']['services']) == {'sl-b', 'client-b', 'swop'}, 'Generated service separation mismatch')
    for name, raw in files.items():
        relative(name)
        require(isinstance(raw, bytes) and len(raw) <= MAX_FILE, 'Invalid generated file')
    require(len(files) <= MAX_FILES - 1 and sum(map(len, files.values())) <= MAX_TOTAL, 'Generated bundle exceeds bounds')
    return files


def manifest_for(profile, files):
    data = profile.data
    n = struct.unpack('>I', profile.bootstrap[8:12])[0]
    model = strict_json(profile.bootstrap[12:12 + n])['model_identity']
    return {'contract': CONTRACT, 'run_id': data['deployment']['run_id'], 'job_id': data['job_id'],
            'hosts': list(HOSTS), 'controller_host': 'rpi5', 'model_id': model,
            'bootstrap_sha256': sha(profile.bootstrap), 'profile_sha256': sha(files['profile.json']),
            'images': {**{s['id']: s['image'] for s in data['services']}, 'swop': data['deployment']['swop_image']},
            'artifacts': [{'path': 'payload/' + name, 'size': len(raw), 'sha256': sha(raw)}
                          for name, raw in sorted(files.items())]}


def envelope(profile, files):
    manifest = manifest_for(profile, files)
    return {'bundle_id': 'sha256:' + sha(encoded(manifest)), 'manifest': manifest}


def fresh_root(path):
    root = checked_path(path)
    require(not root.exists(), 'Destination already exists; never overwrite/reuse a job or checkpoint directory')
    require(root.parent.is_dir(), 'Destination parent must already exist')
    root.mkdir(mode=0o700, exist_ok=False)
    return root


def write_files(root, files):
    for name, raw in sorted(files.items()):
        target = root / relative(name)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


@dataclass
class Verified:
    profile: object
    files: dict
    metadata: dict


def prepare(profile_path, output):
    """Sinh duy nhất một bundle mới; seal ghi cuối và xác minh lại toàn bộ."""
    profile = validate_profile(profile_path)
    files = reference_files(profile)
    metadata = envelope(profile, files)
    root = fresh_root(output)
    # Ghi seal cuối cùng; giữ output dở dang để chẩn đoán, không coi là bundle hợp lệ.
    write_files(root, {'payload/' + p: b for p, b in files.items()})
    write_files(root, {'bundle.json': encoded(metadata)})
    verify(root, metadata['bundle_id'])
    return metadata


def verify(bundle_root, expected_id):
    """Kiểm tra ID tin cậy, inventory, hash và output từ generator SWOP thực tế."""
    require(isinstance(expected_id, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', expected_id) is not None,
            'Trusted expected bundle ID is required')
    root = checked_path(bundle_root)
    all_files, directories = tree(root)
    require('bundle.json' in all_files, 'Missing bundle seal')
    metadata = strict_json(all_files['bundle.json'])
    require(isinstance(metadata, dict) and set(metadata) == {'bundle_id', 'manifest'}, 'Invalid bundle envelope')
    require(metadata['bundle_id'] == expected_id == 'sha256:' + sha(encoded(metadata['manifest'])), 'Bundle identity mismatch')
    manifest = metadata['manifest']
    require(isinstance(manifest, dict) and manifest.get('contract') == CONTRACT, 'Unsupported bundle contract')
    entries = manifest.get('artifacts')
    require(isinstance(entries, list) and 1 <= len(entries) <= MAX_FILES - 1, 'Invalid artifact inventory')
    expected = {'bundle.json'}
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {'path', 'size', 'sha256'}, 'Invalid artifact entry')
        name = relative(entry['path'])
        require(name.startswith('payload/') and name not in expected, 'Duplicate/unsafe artifact inventory')
        expected.add(name)
        require(type(entry['size']) is int and 0 <= entry['size'] <= MAX_FILE
                and isinstance(entry['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', entry['sha256']) is not None,
                'Invalid artifact bounds/digest')
        require(name in all_files and len(all_files[name]) == entry['size'] and sha(all_files[name]) == entry['sha256'],
                'Missing/corrupt artifact: ' + name)
    require(set(all_files) == expected and directories == parent_paths(expected), 'Unexpected or incomplete bundle entries')
    profile = validate_profile(root / 'payload/profile.json')
    reference = reference_files(profile)
    require(metadata == envelope(profile, reference), 'Bundle semantic/generator identity mismatch')
    require(all(all_files['payload/' + name] == raw for name, raw in reference.items()), 'Runtime artifacts differ from accepted SWOP generator')
    return Verified(profile, reference, metadata)


def local_files(verified, host):
    require(host in HOSTS, 'Unknown host selector')
    selected = {}
    for name, raw in verified.files.items():
        if name.startswith(host + '/'):
            selected['deployment/' + name[len(host) + 1:]] = raw
        elif name.split('/')[0] not in HOSTS:
            selected['canonical/' + name] = raw
    selected['bundle.json'] = encoded(verified.metadata)
    return selected


def storage_for(host):
    require(host in HOSTS, 'Unknown host selector')
    paths = ['deployment/data/checkpoints/' + ('client-a' if host == 'rpi5' else 'client-b')]
    if host == 'rpi5':
        paths.append('deployment/swop-output')
    return paths


def ownership_possible(policy):
    require(policy in ('enforce', 'defer'), 'Invalid ownership policy')
    if policy == 'enforce':
        require(os.name == 'posix' and (os.geteuid() == 0 or (os.geteuid(), os.getegid()) == (UID, GID)),
                'Enforced ownership requires POSIX root or UID/GID65532; --defer-ownership is offline staging only')


def set_ownership(root, policy, files, storage):
    # Chỉ thao tác trong thư mục gốc mới được tạo độc quyền cho job.
    # Không sửa cây thư mục có sẵn hoặc chown đệ quy đường dẫn của operator.
    directories = parent_paths(files) | set(storage) | parent_paths(storage)
    paths = [root] + [root / p for p in sorted(directories)] + [root / p for p in sorted(files)]
    for path in paths:
        if path.is_file():
            os.chmod(path, 0o444)
        else:
            rel = path.relative_to(root).as_posix()
            os.chmod(path, 0o700 if path == root or rel in storage else 0o755)
        if policy == 'enforce' and os.geteuid() == 0:
            os.chown(path, UID, GID)
    if policy == 'enforce':
        require(all((p.stat().st_uid, p.stat().st_gid) == (UID, GID) for p in paths), 'Ownership verification failed')


def import_host(bundle_root, host, output, expected_id, ownership='enforce'):
    """Tạo phần cấu hình/storage của đúng host trong thư mục đích chưa tồn tại."""
    verified = verify(bundle_root, expected_id)
    files = local_files(verified, host)
    storage = storage_for(host)
    ownership_possible(ownership)
    root = fresh_root(output)
    write_files(root, files)
    for name in storage:
        (root / name).mkdir(mode=0o700, parents=True, exist_ok=False)
    receipt = {'contract': CONTRACT, 'bundle_id': expected_id, 'host': host,
               'run_id': verified.profile.data['deployment']['run_id'], 'job_id': verified.profile.data['job_id'],
               'storage': storage, 'ownership': {'policy': ownership, 'uid': UID, 'gid': GID,
                                                'verified': ownership == 'enforce'},
               'deployment_authorized': False, 'physical_multihost': 'NOT VERIFIED'}
    # Đặt quyền cấu hình trước khi ghi import receipt/seal cuối cùng.
    set_ownership(root, ownership, files, storage)
    write_files(root, {'import.json': encoded(receipt)})
    os.chmod(root / 'import.json', 0o444)
    if ownership == 'enforce' and os.geteuid() == 0:
        os.chown(root / 'import.json', UID, GID)
    verify_local(root, host, expected_id)
    return receipt


def verify_local(local_root, host, expected_id):
    """Chỉ xác minh import mới chưa có checkpoint; không dùng để resume job."""
    require(host in HOSTS, 'Unknown host selector')
    root = checked_path(local_root)
    files, directories = tree(root)
    require('import.json' in files and 'bundle.json' in files, 'Missing import seal')
    receipt, metadata = strict_json(files['import.json']), strict_json(files['bundle.json'])
    require(isinstance(metadata, dict) and set(metadata) == {'bundle_id', 'manifest'}
            and metadata['bundle_id'] == expected_id == 'sha256:' + sha(encoded(metadata['manifest'])), 'Local bundle identity mismatch')
    profile = validate_profile(root / 'canonical/profile.json')
    reference = reference_files(profile)
    require(metadata == envelope(profile, reference), 'Local profile/bundle mismatch')
    verified = Verified(profile, reference, metadata)
    expected = local_files(verified, host)
    require(all(files.get(p) == b for p, b in expected.items()), 'Local artifact hash/content mismatch')
    require(set(files) == set(expected) | {'import.json'}, 'Unexpected runtime data/files; only fresh pre-deployment imports are verified')
    storage = storage_for(host)
    require(directories == parent_paths(expected) | set(storage) | parent_paths(storage), 'Missing/unsafe/extra local storage directories')
    require(isinstance(receipt, dict) and set(receipt) == {'contract', 'bundle_id', 'host', 'run_id', 'job_id', 'storage',
                                                       'ownership', 'deployment_authorized', 'physical_multihost'}, 'Invalid import receipt')
    require(receipt['contract'] == CONTRACT and receipt['bundle_id'] == expected_id and receipt['host'] == host
            and receipt['run_id'] == profile.data['deployment']['run_id'] and receipt['job_id'] == profile.data['job_id']
            and receipt['storage'] == storage and receipt['deployment_authorized'] is False
            and receipt['physical_multihost'] == 'NOT VERIFIED', 'Import identity/host mismatch')
    own = receipt['ownership']
    require(isinstance(own, dict) and set(own) == {'policy', 'uid', 'gid', 'verified'}
            and own['policy'] in ('enforce', 'defer') and type(own['verified']) is bool
            and own['uid'] == UID and own['gid'] == GID and own['verified'] == (own['policy'] == 'enforce'), 'Invalid ownership receipt')
    if own['verified']:
        require(os.name == 'posix' and all((p.stat().st_uid, p.stat().st_gid) == (UID, GID)
                for p in [root] + [root / n for n in files] + [root / n for n in directories]), 'Actual UID/GID does not match verified ownership')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('validate', 'prepare'):
        item = sub.add_parser(name)
        item.add_argument('profile', type=Path)
        if name == 'prepare':
            item.add_argument('--output', type=Path, required=True)
    for name in ('verify', 'import-host', 'verify-local'):
        item = sub.add_parser(name)
        item.add_argument('path', type=Path)
        item.add_argument('--expected-id', required=True, help='Bundle ID nhận qua kênh tin cậy độc lập')
        if name != 'verify':
            item.add_argument('--host', choices=HOSTS, required=True)
        if name == 'import-host':
            item.add_argument('--output', type=Path, required=True)
            item.add_argument('--defer-ownership', action='store_true', help='Chỉ staging offline, không xác nhận quyền UID/GID native')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            profile = validate_profile(args.profile)
            result = {'status': 'VALID_OFFLINE', 'run_id': profile.data['deployment']['run_id'],
                      'job_id': profile.data['job_id'], 'hosts': list(HOSTS), 'controller_host': 'rpi5'}
        elif args.command == 'prepare':
            result = {'status': 'PREPARED_OFFLINE', **prepare(args.profile, args.output)}
        elif args.command == 'verify':
            result = {'status': 'VERIFIED_OFFLINE', **verify(args.path, args.expected_id).metadata}
        elif args.command == 'import-host':
            result = {'status': 'IMPORTED_OFFLINE', **import_host(args.path, args.host, args.output, args.expected_id,
                       'defer' if args.defer_ownership else 'enforce')}
        else:
            result = {'status': 'LOCAL_VERIFIED_OFFLINE', **verify_local(args.path, args.host, args.expected_id)}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({'status': 'REJECTED', 'error': str(exc)[:512], 'docker_actions': []}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
