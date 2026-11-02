"""Kiểm tra số học bằng NumPy tùy chọn; không chạy TensorFlow, training hay mạng."""
import importlib.util
from pathlib import Path
import unittest

HAS_NUMPY = importlib.util.find_spec('numpy') is not None

@unittest.skipUnless(HAS_NUMPY, 'Existing NumPy needed for optional arithmetic checks')
class NumericReplayMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy
        cls.np = numpy
        spec = importlib.util.spec_from_file_location('verify_fedavg', Path(__file__).resolve().parents[1] / 'config/verify_fedavg.py')
        cls.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.module)

    def test_sample_weighted_arithmetic(self):
        np = self.np
        result = self.module.average([((np.array([1., 4.], dtype=np.float32),), 1),
                                      ((np.array([3., 8.], dtype=np.float32),), 3)])
        np.testing.assert_array_equal(result[0], np.array([2.5, 7.], dtype=np.float32))
        self.assertEqual(result[0].dtype, np.float32)

    def test_multiple_shapes_and_unequal_counts(self):
        np = self.np
        result = self.module.average([((np.ones((2, 2), np.float32), np.array([2.], np.float32)), 16),
                                      ((np.full((2, 2), 4., np.float32), np.array([5.], np.float32)), 32)])
        np.testing.assert_array_equal(result[0], np.full((2, 2), 3., np.float32))
        np.testing.assert_array_equal(result[1], np.array([4.], np.float32))

if __name__ == '__main__':
    unittest.main(verbosity=2)
