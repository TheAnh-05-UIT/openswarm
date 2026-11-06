"""Chỉ chạy Compose config trên bản sao repo nằm ở đường dẫn khác.

Không gọi up/down/run/pull và không cần kết nối Docker daemon.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
def main():
    environment = dict(line.split('=', 1) for line in (ROOT / '.env.example').read_text(encoding='utf-8').splitlines()
                       if line and not line.startswith('#'))
    environment.update(RUN_ID='20261101t120000z-1234abcd', EXCLUSIVE_OWNER_ACK='true')
    results = []
    work = Path(os.environ.get('OPENSWARM_TEST_WORK', ROOT / 'tests/tmp')); work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        repo = Path(tmp) / 'relocated'
        # Chỉ sao chép đầu vào triển khai để kiểm tra repo không lệ thuộc thư mục gốc.
        repo.mkdir()
        shutil.copy2(ROOT / 'compose.yaml', repo / 'compose.yaml')
        shutil.copytree(ROOT / 'config', repo / 'config', ignore=shutil.ignore_patterns('__pycache__'))
        for name, changes, allowed in [('default', {}, True), ('other-tags', {'SN_VERSION': 'reviewed-v2'}, True),
                                        ('missing-run', {'RUN_ID': ''}, False), ('missing-owner-ack', {'EXCLUSIVE_OWNER_ACK': ''}, False)]:
            env = repo / '.env'
            env.write_text(''.join(k + '=' + v + '\n' for k, v in {**environment, **changes}.items()), encoding='utf-8')
            command = ['docker', 'compose', '--project-directory', str(repo), '--env-file', str(env), '-f', str(repo / 'compose.yaml')]
            proc = subprocess.run(command + ['config', '--quiet'], capture_output=True, text=True)
            results.append({'case': name, 'mode': 'docker compose config --quiet', 'exit_code': proc.returncode,
                            'expected_accept': allowed, 'status': 'PASS' if (proc.returncode == 0) == allowed else 'FAIL',
                            'stderr': proc.stderr.strip()})
            if allowed and proc.returncode == 0:
                rendered = subprocess.run(command + ['config', '--format', 'json'], capture_output=True, text=True)
                if rendered.returncode:
                    raise RuntimeError(rendered.stderr)
                data = json.loads(rendered.stdout)
                images = sorted({s['image'] for s in data['services'].values()})
                assert not any('build' in s or 'ports' in s for s in data['services'].values())
                assert data['services']['round-controller']['command'][0] == 'orchestrate'
                assert len(images) == 4
                if name == 'default':
                    assert set(images) == {'24520115/independent-' + x + ':v0.1.0-arm64' for x in ['sn', 'sl', 'user-tf', 'swop']}
                results[-1]['images'] = images
        # Kiểm tra cú pháp profile tools mà không khởi động container xác minh.
        (repo / '.env').write_text(''.join(k + '=' + v + '\n' for k, v in environment.items()), encoding='utf-8')
        proc = subprocess.run(command + ['--profile', 'tools', 'config', '--quiet'], capture_output=True, text=True)
        results.append({'case': 'tools-profile', 'exit_code': proc.returncode, 'status': 'PASS' if proc.returncode == 0 else 'FAIL', 'stderr': proc.stderr.strip()})
    report = {'scope': 'static parsing only, relocated config; no image pull or containers',
              'required_static_secrets': 'NOT APPLICABLE: these images require none; missing RUN_ID/owner ACK are tested',
              'runtime': 'NOT VERIFIED', 'results': results}
    evidence = Path(os.environ.get('OPENSWARM_TEST_EVIDENCE', ROOT / 'tests/evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'compose-validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for result in results:
        print(result['case'], result['status'], result['exit_code'])
    if any(r['status'] != 'PASS' for r in results):
        raise SystemExit(1)

if __name__ == '__main__':
    main()
