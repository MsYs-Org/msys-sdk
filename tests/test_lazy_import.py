from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LazyPackageImportTests(unittest.TestCase):
    def test_ipc_first_package_import_does_not_load_tk(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys,msys_sdk; "
                "assert msys_sdk.MsysClient; "
                "assert 'tkinter' not in sys.modules; "
                "assert 'msys_sdk.tk_app' not in sys.modules",
            ],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lazy_public_names_keep_the_existing_api(self) -> None:
        import msys_sdk

        self.assertTrue(msys_sdk.Catalog)
        self.assertTrue(msys_sdk.bind_tk_input_method)
        self.assertIn("TouchApplication", dir(msys_sdk))


if __name__ == "__main__":
    unittest.main()
