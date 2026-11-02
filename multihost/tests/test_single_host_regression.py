"""Chạy regression một host trên BẢN SAO; không sửa cấu hình triển khai gốc."""
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT.parent
AVAILABLE = (importlib.util.find_spec('swop') is not None and importlib.util.find_spec('numpy') is not None
             and bool(os.environ.get('BASH_BIN') or shutil.which('bash')))


@unittest.skipUnless(AVAILABLE, 'Existing SWOP/NumPy/Bash required; no installation or Docker fallback')
class SingleHostRegression(unittest.TestCase):
    def test_accepted_offline_suite_in_relocated_copy(self):
        work = ROOT / 'tests/tmp'; work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as tmp:
            target = Path(tmp) / 'single-host-copy'; target.mkdir()
            assert target.resolve().is_relative_to(work.resolve())
            for name in ('compose.yaml', '.env.example', '.gitignore'):
                shutil.copy2(SINGLE / name, target / name)
            for name in ('config', 'scripts'):
                shutil.copytree(SINGLE / name, target / name, ignore=shutil.ignore_patterns('__pycache__'))
            (target / 'tests').mkdir()
            for name in ('test_deployment.py', 'test_numeric.py'):
                shutil.copy2(SINGLE / 'tests' / name, target / 'tests' / name)
            suite = unittest.TestLoader().discover(str(target / 'tests'))
            stream = io.StringIO()
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
            # Kết quả test ghi vào thư mục evidence bị loại khỏi Git.
            (ROOT / 'evidence').mkdir(exist_ok=True)
            (ROOT / 'evidence/single-host-regression.txt').write_text(stream.getvalue(), encoding='utf-8')
            self.assertEqual(result.testsRun, 51, stream.getvalue())
            self.assertEqual(len(result.skipped), 0, stream.getvalue())
            self.assertTrue(result.wasSuccessful(), stream.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
