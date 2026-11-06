"""Kiểm tra file và tài liệu cục bộ; không gọi Docker hoặc Git."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
def main():
    results = []
    def check(name, ok, detail=''):
        results.append({'check': name, 'status': 'PASS' if ok else 'FAIL', 'detail': detail})
    paths = [p for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.relative_to(ROOT).parts
             and '__pycache__' not in p.relative_to(ROOT).parts and 'tmp' not in p.relative_to(ROOT).parts]
    required = ['compose.yaml', '.env.example', '.gitignore', '.gitattributes', 'README.md', 'NOTICE.md', 'LICENSE',
                'secrets/.gitkeep', 'config/assets/initial.iutfw', 'config/profile.template.json',
                'config/initialize.py', 'config/verify.py', 'config/verify_fedavg.py',
                'scripts/check.sh', 'scripts/start.sh', 'scripts/stop.sh', 'scripts/logs.sh',
                'docs/single-host.md', 'docs/multi-host.md', 'docs/install-docker.md',
                'docs/remove-deployment.md', 'config/image-manifest.json']
    # Bốn tài liệu vận hành là toàn bộ docs/, tránh tái tạo các hướng dẫn trùng lặp.
    expected_docs = {'single-host.md', 'multi-host.md', 'install-docker.md', 'remove-deployment.md'}
    check('required deploy files present', all((ROOT / p).is_file() for p in required)
          and {p.name for p in (ROOT / 'docs').iterdir()} == expected_docs)
    check('no symlink escape', all(not p.is_symlink() and p.resolve().is_relative_to(ROOT) for p in paths))
    compose = json.loads((ROOT / 'compose.yaml').read_text(encoding='utf-8'))
    check('only image references; no build', all('image' in s and 'build' not in s for s in compose['services'].values()))
    check('no runtime dependencies outside repo', all(v['type'] == 'volume' or
        (v['source'] == './config' and (ROOT / 'config').is_dir() and v['read_only'])
        for s in compose['services'].values() for v in s.get('volumes', [])))
    runtime_text = (ROOT / 'compose.yaml').read_text(encoding='utf-8') + '\n' + '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT / 'config').glob('*.py'))
    check('no Windows or task-workspace runtime paths', not re.search(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]|task-arm-007[cdef][/\\]|\.\./independent-', runtime_text))
    check('no application source packages', not any((ROOT / name).exists() for name in ('sn_mvp', 'sl_runtime', 'user_tf', 'swop', 'openswarm_api')))
    bad = []
    for path in ROOT.rglob('*.md'):
        if '.git' in path.relative_to(ROOT).parts:
            continue
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if target.startswith(('https:', 'http:', '#')):
                continue
            target_path = path.parent / target.split('#')[0]
            own_report = ROOT / 'tests/evidence/repository-checks.json'
            if not target_path.exists() and target_path.resolve() != own_report.resolve():
                bad.append(str(path.relative_to(ROOT)) + ' -> ' + target)
    check('local Markdown links resolve', not bad, '; '.join(bad))
    syntax = []
    for path in paths:
        if path.suffix == '.py':
            try:
                ast.parse(path.read_text(encoding='utf-8'), filename=str(path.relative_to(ROOT)))
            except SyntaxError as exc:
                syntax.append(str(exc))
    check('all Python helpers/tests parse without importing runtime', not syntax, '; '.join(syntax))
    check('shell files use LF', all(b'\r\n' not in p.read_bytes() for p in (ROOT / 'scripts').glob('*.sh')))
    check('no actual .env shipped', not (ROOT / '.env').exists())
    check('no secret payload shipped', [p.name for p in (ROOT / 'secrets').iterdir() if p.is_file()] == ['.gitkeep'])
    env = dict(line.split('=', 1) for line in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#'))
    check('all and only consumed env keys', set(env) == set(re.findall(r'\$\{([A-Z][A-Z0-9_]*)', (ROOT / 'compose.yaml').read_text(encoding='utf-8'))))
    check('namespace and four explicit tags', env['IMAGE_NAMESPACE'] == '24520115' and
          all(env[k] == 'v0.1.0-arm64' for k in ['SN_VERSION', 'SL_VERSION', 'USER_TF_VERSION', 'SWOP_VERSION']))
    check('single default controller', sum('orchestrate' in s.get('command', []) for s in compose['services'].values()) == 1)
    check('bootstrap data checksum', hashlib.sha256((ROOT / 'config/assets/initial.iutfw').read_bytes()).hexdigest() ==
          '590525b9b37b66b74a21c492a910a27b30ca030d2d15bac64a1b4203ae314702')
    check('Monitoring excluded', not any('monitoring' in name for name in compose['services']))
    status = 'PASS' if all(r['status'] == 'PASS' for r in results) else 'FAIL'
    output = {'status': status, 'scope': 'Offline file checks; no Docker/Git, native or runtime claim',
              'checks': results, 'required_static_secrets': 'N/A; current services require none'}
    evidence = Path(os.environ.get('OPENSWARM_TEST_EVIDENCE', ROOT / 'tests/evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'repository-checks.json').write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
    print(status, len(results), 'repository checks')
    for item in results:
        if item['status'] != 'PASS': print(item)
    raise SystemExit(status != 'PASS')

if __name__ == '__main__':
    main()
