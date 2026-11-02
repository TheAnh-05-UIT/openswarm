"""Chuẩn bị cấu hình triển khai bên trong image SWOP, không cài SWOP lên host.

Dùng bộ sinh cấu hình và launcher có sẵn trong image; repo không chứa mã ML.
Chỉ ghi vào các named volume MỚI thuộc project đang được khởi tạo.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re

UID = GID = 65532
ASSET_SHA = '590525b9b37b66b74a21c492a910a27b30ca030d2d15bac64a1b4203ae314702'
ROOTS = ['/deployment', '/checkpoint-a', '/checkpoint-b', '/controller-data']
RUN_PATTERN = r'[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}'

def require(ok, message):
    if not ok:
        raise ValueError(message)

def number(env, name, low, high, decimal=False):
    text = env[name]
    require(bool(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?' if decimal else r'[0-9]+', text)), name + ': invalid numeric literal')
    value = float(text) if decimal else int(text)
    require(low <= value <= high, name + ': outside supported limits')
    return value

def make_profile(template, env):
    """Giới hạn cấu hình ở hai round nhỏ; chỉ kiểm tra dữ liệu cục bộ."""
    data = json.loads(json.dumps(template))
    require(bool(re.fullmatch(RUN_PATTERN, env['RUN_ID'])), 'RUN_ID must be UTC timestamp + eight hex characters')
    require(env['EXCLUSIVE_OWNER_ACK'] == 'true', 'A single trusted round writer must be acknowledged')
    namespace = env['IMAGE_NAMESPACE']
    require(bool(re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,63}', namespace)), 'Invalid Docker Hub namespace')
    images = {}
    for role, version in [('sn', 'SN_VERSION'), ('sl', 'SL_VERSION'), ('user-tf', 'USER_TF_VERSION'), ('swop', 'SWOP_VERSION')]:
        tag = env[version]
        require(bool(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag)) and tag.lower() != 'latest', 'Explicit non-latest version required')
        images[role] = namespace + '/independent-' + role + ':' + tag
    ports = {name: number(env, name, 1024, 65535) for name in ['SN_PORT', 'SL_PORT', 'CONTROLLER_PORT']}
    require(ports['SN_PORT'] != ports['CONTROLLER_PORT'], 'Controller shares SN network namespace; ports must differ')
    timeout = number(env, 'ROUND_TIMEOUT', 30, 1800)
    startup = number(env, 'STARTUP_TIMEOUT', 30, 1800)
    rounds = number(env, 'ROUNDS', 2, 2)
    data['job_id'] = 'pi-' + env['RUN_ID']
    data['deployment'].update(run_id=env['RUN_ID'], swop_image=images['swop'])
    for service in data['services']:
        role = service['role']
        service['image'] = images['user-tf' if role == 'user_tf' else role]
        params = service['params']
        if role in ('sn', 'sl'):
            port = ports['SN_PORT' if role == 'sn' else 'SL_PORT']
            service.update(port=port, published_port=port)
        if role == 'sl':
            params.update(round_timeout=timeout, startup_timeout=startup)
        elif role == 'user_tf':
            suffix = 'A' if service['id'] == 'client-a' else 'B'
            params.update(samples=number(env, 'SAMPLES_' + suffix, 1, 256),
                          data_seed=number(env, 'DATA_SEED_' + suffix, 0, 2147483647),
                          epochs=number(env, 'EPOCHS', 1, 10), batch_size=number(env, 'BATCH_SIZE', 1, 64),
                          learning_rate=number(env, 'LEARNING_RATE', 0.0001, 0.1, True),
                          round_ids=list(range(1, rounds + 1)), startup_timeout=startup,
                          round_wait_timeout=max(startup, timeout) + 120, training_timeout=timeout)
            require(math.ceil(params['samples'] / params['batch_size']) * params['epochs'] <= 1024,
                    'Training settings exceed the User-TF 1024-minibatch limit')
    data['orchestration'].update(port=ports['CONTROLLER_PORT'], published_port=ports['CONTROLLER_PORT'],
                                 startup_timeout=startup, round_timeout=timeout)
    return data

def encoded(data):
    return (json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()

def prepare_mounts(roots=ROOTS):
    """Không chown đệ quy, ghi đè, xóa hoặc truy cập ngoài các mount đã định."""
    for root in map(Path, roots):
        require(root.is_dir() and not root.is_symlink(), 'Required named volume missing: ' + str(root))
        require(not any(root.iterdir()), 'Volume not empty; refuse reuse: ' + str(root))
    require(os.getuid() == 0, 'Bootstrap requires narrowly scoped root privileges on NEW named volumes only')
    for root in map(Path, roots):
        # Volume mới thuộc root: đặt quyền trước khi chuyển chủ sở hữu.
        os.chmod(root, 0o700)
        os.chown(root, UID, GID)
    os.setgroups([])
    os.setgid(GID)
    os.setuid(UID)
    require(os.getuid() == UID and os.getgid() == GID, 'Privilege drop failed')

def generate(data, asset, target):
    from swop.profile import load_profile
    from swop.generate import bundle, write_bundle
    target = Path(target)
    require(not target.exists(), 'Output must be new; automatic resume is not supported')
    inputs = target.parent / 'input'
    inputs.mkdir(exist_ok=False)
    (inputs / 'assets').mkdir()
    (inputs / 'assets/initial.iutfw').write_bytes(asset)
    (inputs / 'profile.json').write_bytes(encoded(data))
    profile = load_profile(inputs / 'profile.json')
    files, _ = bundle(profile)
    for service in data['services']:
        if service['role'] == 'sl':
            name = service['host'] + '/config/' + service['id'] + '.json'
            cfg = json.loads(files[name])
            cfg['initial_weights'] = str(target / 'assets/initial.iutfw')
            files[name] = encoded(cfg)
    # Cập nhật inventory theo các đường dẫn cấu hình đã chuyển đổi.
    manifest = json.loads(files['deployment-manifest.json'])
    manifest['payload_files'] = [{'path': p, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
                         for p, b in sorted(files.items()) if p != 'deployment-manifest.json']
    files['deployment-manifest.json'] = encoded(manifest)
    write_bundle(files, target)
    return files

def main():
    config = Path(__file__).resolve().parent
    data = make_profile(json.loads((config / 'profile.template.json').read_text(encoding='utf-8')), os.environ)
    raw = (config / 'assets/initial.iutfw').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ASSET_SHA, 'Numeric bootstrap SHA-256 mismatch')
    # Kiểm tra schema và quan hệ cấu hình trước khi ghi vào volume mới.
    from swop.profile import validate
    validate(data)
    prepare_mounts()
    generate(data, raw, '/deployment/bundle')
    Path('/deployment/READY.json').write_bytes(encoded({'contract_version': 1, 'run_id': os.environ['RUN_ID'],
        'uid': UID, 'gid': GID, 'profile_sha256': hashlib.sha256(encoded(data)).hexdigest()}))
    print(json.dumps({'status': 'BUNDLE_READY', 'run_id': os.environ['RUN_ID'], 'uid': os.getuid()}), flush=True)

if __name__ == '__main__':
    main()
