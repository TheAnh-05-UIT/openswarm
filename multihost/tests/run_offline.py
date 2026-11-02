"""Chạy tests multi-host và regression một host trên bản sao repo tự chứa.

Không khởi động service. Docker thật chỉ được gọi để kiểm tra Compose config.
Package SWOP tùy chọn dành cho máy phát triển, không phải phụ thuộc trên board.
"""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def worker(copy_root, source, evidence):
    if source:
        sys.path.insert(0, str(source))
    stream = io.StringIO()
    suite = unittest.TestLoader().discover(str(copy_root / 'multihost/tests'))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (evidence / 'tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    for name in ('single-host-regression.txt',):
        generated = copy_root / 'multihost/evidence' / name
        if generated.exists(): shutil.copy2(generated, evidence / name)
    summary = {'scope': 'Offline relocated-copy tests; real Docker CLI used only for Compose CONFIG; operational/native probes use fakes',
               'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': len(result.skipped), 'success': result.wasSuccessful()}
    (evidence / 'test-summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(stream.getvalue()[-3500:]); print(json.dumps(summary))
    return 0 if result.wasSuccessful() and not result.skipped else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--swop-source', type=Path, help='Tùy chọn: thư mục package SWOP đã có trên máy phát triển; không tự cài')
    parser.add_argument('--bash', help='Đường dẫn Bash đã có sẵn; Windows nên dùng Git Bash')
    parser.add_argument('--worker-copy', type=Path)
    args = parser.parse_args()
    source = args.swop_source.resolve() if args.swop_source else None
    if source and not (source / 'swop/profile.py').is_file(): parser.error('Không tìm thấy package SWOP tại đường dẫn đã chọn')
    evidence = ROOT / 'evidence'; evidence.mkdir(exist_ok=True)
    if args.worker_copy: return worker(args.worker_copy, source, evidence)
    work = ROOT / 'tests/tmp'; work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='c-', dir=work) as tmp:
        repo = Path(tmp) / 'repo'; repo.mkdir()
        assert repo.resolve().is_relative_to(work.resolve())
        for name in ('compose.yaml', '.env.example', '.gitignore'): shutil.copy2(ROOT.parent / name, repo / name)
        for name in ('config', 'scripts'):
            shutil.copytree(ROOT.parent / name, repo / name, ignore=shutil.ignore_patterns('__pycache__'))
        (repo / 'tests').mkdir()
        for name in ('test_deployment.py', 'test_numeric.py'): shutil.copy2(ROOT.parent / 'tests' / name, repo / 'tests' / name)
        shutil.copytree(ROOT, repo / 'multihost', ignore=shutil.ignore_patterns('tmp', 'work', '__pycache__', 'evidence*'))
        (repo / 'multihost/evidence').mkdir()
        environment = os.environ.copy()
        if args.bash: environment['BASH_BIN'] = args.bash
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker-copy', str(repo)]
        if source:
            command.extend(['--swop-source', str(source)])
        return subprocess.run(command, env=environment, check=False).returncode


if __name__ == '__main__': raise SystemExit(main())
