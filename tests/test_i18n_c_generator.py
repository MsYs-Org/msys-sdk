from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

from msys_sdk import Catalog
from msys_sdk.i18n_c import render_c_header


class CHeaderGeneratorTests(unittest.TestCase):
    def catalog(self) -> Catalog:
        return Catalog.from_mapping(
            {
                "schema": "msys.i18n.catalog.v1",
                "id": "org.example.generated",
                "default_locale": "en-US",
                "messages": {
                    "en-US": {"welcome": "Hello, {name}"},
                    "zh-CN": {"welcome": "你好，{name}"},
                },
            }
        )

    def test_output_is_static_utf8_c_without_runtime_json_dependency(self) -> None:
        rendered = render_c_header(self.catalog(), "example_catalog")
        self.assertIn("static const msys_i18n_catalog example_catalog", rendered)
        self.assertIn('"\\xe4"', rendered)
        self.assertNotIn("你好", rendered)
        self.assertNotIn("json", rendered.lower().splitlines()[3:])

    def test_rejects_non_identifier_symbol(self) -> None:
        with self.assertRaises(ValueError):
            render_c_header(self.catalog(), "bad-symbol")

    @unittest.skipUnless(shutil.which("cc"), "C compiler is unavailable")
    def test_generated_header_compiles_as_c(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "catalog.h").write_text(
                render_c_header(self.catalog(), "example_catalog"),
                encoding="utf-8",
            )
            source = directory / "main.c"
            source.write_text(
                '#include "catalog.h"\n'
                "int main(void) {\n"
                "  return msys_i18n_lookup(&example_catalog, \"zh-CN\", \"welcome\") == 0;\n"
                "}\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    shutil.which("cc") or "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(root / "include"),
                    str(source),
                    str(root / "src" / "i18n.c"),
                    "-o",
                    str(directory / "generated-test"),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
