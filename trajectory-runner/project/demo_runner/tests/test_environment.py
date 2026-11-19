import pathlib
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from runtime_environment import check_runtime
import autonomous_harness as h


class EnvironmentTests(unittest.TestCase):
    def test_old_pandas_is_rejected(self):
        versions = {"numpy": "1.26.4", "pandas": "1.3.5"}
        with patch("runtime_environment.importlib.import_module", side_effect=lambda n: types.SimpleNamespace(__version__=versions[n])):
            with self.assertRaisesRegex(RuntimeError, "pandas 1.3.5"):
                check_runtime()

    def test_old_numpy_stops_doctor_before_key_check(self):
        versions = {"numpy": "1.21.5", "pandas": "2.2.3"}
        with patch("runtime_environment.importlib.import_module", side_effect=lambda n: types.SimpleNamespace(__version__=versions[n])), \
             patch("model_client._key") as key:
            with self.assertRaisesRegex(RuntimeError, "numpy 1.21.5"):
                h.doctor({})
            key.assert_not_called()

    def test_real_runtime_and_intraday_series_operation(self):
        versions = check_runtime()
        self.assertEqual(versions, {"numpy": "1.26.4", "pandas": "2.2.3"})
        import numpy as np
        import pandas as pd
        minute = pd.Series([110., 90.], name=(144, 1000))
        previous = pd.Series([100., 100.], name=np.int64(143))
        self.assertTrue(np.allclose((minute / previous - 1).to_numpy(), [.1, -.1]))


if __name__ == "__main__":
    unittest.main()
