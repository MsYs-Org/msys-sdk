from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from msys_sdk.app_manifest import (
    AppManifestError,
    SUPPORTED_RUNTIMES,
    create_application_manifest,
    main,
    write_manifest,
)


class AppManifestTests(unittest.TestCase):
    def test_supported_runtime_defaults_are_language_neutral(self) -> None:
        expected = {
            "python": (
                ["python", "@package/files/app/main.py"],
                {"PYTHONUNBUFFERED": "1"},
                5000,
            ),
            "tk": (
                ["python", "@package/files/app/main.py"],
                {"PYTHONUNBUFFERED": "1"},
                5000,
            ),
            "c": (["@package/files/bin/app"], None, 5000),
            "cpp": (["@package/files/bin/app"], None, 5000),
            "qt": (
                ["@package/files/bin/app"],
                {
                    "QT_QPA_PLATFORM": "xcb",
                    "QT_PLUGIN_PATH": "files/runtime/qt/plugins",
                    "LD_LIBRARY_PATH": "files/runtime/qt/lib",
                },
                8000,
            ),
            "electron": (
                [
                    "@package/files/runtime/electron/electron",
                    "--no-sandbox",
                    "@package/files/app",
                ],
                {"ELECTRON_ENABLE_LOGGING": "1"},
                10000,
            ),
        }
        self.assertEqual(set(SUPPORTED_RUNTIMES), set(expected))
        for runtime, (command, environment, timeout) in expected.items():
            with self.subTest(runtime=runtime):
                manifest = create_application_manifest(
                    package_id=f"org.example.{runtime}", runtime=runtime
                )
                component = manifest["components"][0]
                self.assertEqual(component["runtime"], runtime)
                self.assertEqual(component["exec"], command)
                self.assertEqual(component.get("env"), environment)
                self.assertEqual(component["readiness"], {"mode": "exec", "timeout_ms": timeout})

    def test_default_graphical_application_has_stable_identity(self) -> None:
        manifest = create_application_manifest(
            package_id="org.example.my_clock",
            runtime="tk",
            version="1.2.3-beta.1+arm64",
        )
        self.assertEqual(manifest["schema"], "msys.manifest.v1")
        self.assertEqual(
            manifest["package"],
            {
                "id": "org.example.my_clock",
                "name": "My Clock",
                "version": "1.2.3-beta.1+arm64",
                "kind": "application",
                "summary": "My Clock, an MSYS tk component",
            },
        )
        component = manifest["components"][0]
        self.assertEqual(component["lifecycle"], "manual")
        self.assertEqual(component["restart"], "never")
        self.assertEqual(component["activation"], {"launchable": True})
        self.assertEqual(component["permissions"], ["display:x11"])
        self.assertEqual(
            component["windowing"],
            {
                "system": "x11",
                "display": "inherit",
                "mode": "window",
                "title": "My Clock",
                "identity": {
                    "app_id": "org.example.my_clock",
                    "x11_wm_class": "org.example.my_clock",
                    "x11_wm_instance": "main",
                },
            },
        )

    def test_headless_defaults_to_not_launchable(self) -> None:
        component = create_application_manifest(
            package_id="org.example.worker",
            runtime="cpp",
            graphical=False,
        )["components"][0]
        self.assertNotIn("windowing", component)
        self.assertNotIn("permissions", component)
        self.assertEqual(component["activation"], {"launchable": False})
        self.assertEqual(component["lifecycle"], "manual")

    def test_headless_launchable_can_be_explicit(self) -> None:
        component = create_application_manifest(
            package_id="org.example.command",
            runtime="c",
            graphical=False,
            launchable=True,
        )["components"][0]
        self.assertEqual(component["activation"], {"launchable": True})

    def test_all_provide_kinds_select_provider_defaults(self) -> None:
        component = create_application_manifest(
            package_id="org.example.provider",
            runtime="python",
            graphical=False,
            roles=["launcher"],
            interfaces=["org.example.echo.v1"],
            capabilities=["example.echo:v1"],
        )["components"][0]
        self.assertEqual(component["lifecycle"], "on-demand")
        self.assertEqual(component["restart"], "on-failure")
        self.assertEqual(
            component["readiness"], {"mode": "mipc-ready", "timeout_ms": 5000}
        )
        self.assertEqual(component["activation"], {"launchable": False})
        self.assertEqual(
            component["provides"],
            [
                {"role": "launcher", "exclusive": True},
                {"interface": "org.example.echo.v1", "exclusive": False},
                {"capability": "example.echo:v1", "exclusive": False},
            ],
        )

    def test_capability_only_is_a_provider(self) -> None:
        component = create_application_manifest(
            package_id="org.example.discovery",
            runtime="cpp",
            graphical=False,
            capabilities=["device.sensor:v1"],
        )["components"][0]
        self.assertEqual(component["lifecycle"], "on-demand")
        self.assertEqual(component["readiness"]["mode"], "mipc-ready")
        self.assertFalse(component["activation"]["launchable"])

    def test_explicit_lifecycle_readiness_command_and_permissions(self) -> None:
        component = create_application_manifest(
            package_id="org.example.service",
            runtime="electron",
            lifecycle="background",
            restart="always",
            readiness="mipc-ready",
            timeout_ms=12345,
            window_mode="overlay",
            display=":24",
            launchable=False,
            permissions=["mipc.call:msys.core", "display:x11"],
            entrypoint="@package/files/bin/wrapper",
            exec_args=["--mode", "touch"],
        )["components"][0]
        self.assertEqual(
            component["exec"],
            ["@package/files/bin/wrapper", "--mode", "touch"],
        )
        self.assertEqual(component["lifecycle"], "background")
        self.assertEqual(component["restart"], "always")
        self.assertEqual(
            component["readiness"],
            {"mode": "mipc-ready", "timeout_ms": 12345},
        )
        self.assertEqual(component["permissions"], ["mipc.call:msys.core", "display:x11"])
        self.assertEqual(component["windowing"]["display"], ":24")
        self.assertEqual(component["windowing"]["mode"], "overlay")

    def test_generator_accepts_unicode_display_name(self) -> None:
        manifest = create_application_manifest(
            package_id="org.example.settings", runtime="qt", name="设置"
        )
        self.assertEqual(manifest["package"]["name"], "设置")
        self.assertEqual(manifest["components"][0]["windowing"]["title"], "设置")

    def test_invalid_scalar_fields_fail_with_developer_error(self) -> None:
        cases = (
            ({"package_id": "bad", "runtime": "python"}, "package id"),
            ({"package_id": "org.Example.bad", "runtime": "python"}, "package id"),
            ({"package_id": "org.example.bad", "runtime": "rust"}, "runtime"),
            ({"package_id": "org.example.bad", "runtime": "python", "version": 1}, "semantic version"),
            ({"package_id": "org.example.bad", "runtime": "python", "version": "1.0"}, "semantic version"),
            ({"package_id": "org.example.bad", "runtime": "python", "component": "1bad"}, "component id"),
            ({"package_id": "org.example.bad", "runtime": "python", "kind": "library"}, "kind"),
            ({"package_id": "org.example.bad", "runtime": "python", "name": ""}, "name"),
            ({"package_id": "org.example.bad", "runtime": "python", "graphical": "yes"}, "graphical"),
            ({"package_id": "org.example.bad", "runtime": "python", "launchable": 1}, "launchable"),
            ({"package_id": "org.example.bad", "runtime": "python", "lifecycle": "boot"}, "lifecycle"),
            ({"package_id": "org.example.bad", "runtime": "python", "restart": "sometimes"}, "restart"),
            ({"package_id": "org.example.bad", "runtime": "python", "readiness": "socket"}, "readiness"),
            ({"package_id": "org.example.bad", "runtime": "python", "timeout_ms": True}, "timeout"),
            ({"package_id": "org.example.bad", "runtime": "python", "timeout_ms": 0}, "timeout"),
            ({"package_id": "org.example.bad", "runtime": "python", "display": "24"}, "display"),
            ({"package_id": "org.example.bad", "runtime": "python", "display": ":" + "1" * 128}, "display"),
            ({"package_id": "org.example.bad", "runtime": "python", "window_mode": "desktop"}, "window mode"),
            ({"package_id": "org.example.bad", "runtime": "python", "permissions": ["p" * 257]}, "permission"),
            ({"package_id": "org.example.bad", "runtime": "python", "exec_args": ["x"] * 255}, "256 arguments"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(AppManifestError, message):
                    create_application_manifest(**arguments)

    def test_invalid_or_duplicate_collections_fail(self) -> None:
        cases = (
            ({"roles": "launcher"}, "iterable of strings"),
            ({"roles": ["Launcher"]}, "invalid role"),
            ({"roles": ["launcher", "launcher"]}, "duplicate role"),
            ({"interfaces": ["bad/interface"]}, "invalid interface"),
            ({"capabilities": ["Bad"]}, "invalid capability"),
            ({"permissions": ["bad permission"]}, "invalid permission"),
            ({"permissions": ["display:x11", "display:x11"]}, "duplicate permission"),
            ({"exec_args": "--debug"}, "iterable of strings"),
            ({"exec_args": [1]}, "must be strings"),
        )
        base = {"package_id": "org.example.bad", "runtime": "python"}
        for override, message in cases:
            with self.subTest(override=override):
                with self.assertRaisesRegex(AppManifestError, message):
                    create_application_manifest(**base, **override)

    def test_exec_arguments_preserve_order_and_duplicates(self) -> None:
        component = create_application_manifest(
            package_id="org.example.argv",
            runtime="cpp",
            exec_args=["-v", "-v", "--literal value"],
        )["components"][0]
        self.assertEqual(
            component["exec"],
            ["@package/files/bin/app", "-v", "-v", "--literal value"],
        )

    def test_write_manifest_is_atomic_and_refuses_overwrite(self) -> None:
        manifest = create_application_manifest(
            package_id="org.example.write", runtime="python"
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "nested" / "manifest.json"
            self.assertEqual(write_manifest(manifest, destination), destination)
            self.assertEqual(
                json.loads(destination.read_text(encoding="utf-8")), manifest
            )
            self.assertTrue(destination.read_bytes().endswith(b"\n"))
            self.assertEqual(list(destination.parent.glob(".*.tmp")), [])
            with self.assertRaisesRegex(AppManifestError, "refusing to overwrite"):
                write_manifest(manifest, destination)
            replacement = create_application_manifest(
                package_id="org.example.write", runtime="python", version="2.0.0"
            )
            write_manifest(replacement, destination, force=True)
            self.assertEqual(
                json.loads(destination.read_text(encoding="utf-8"))["package"]["version"],
                "2.0.0",
            )

    def test_cli_writes_file_and_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "manifest.json"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--id", "org.example.cli", "--runtime", "cpp",
                            "--headless", "--capability", "example.worker:v1",
                            "--output", str(destination),
                        ]
                    ),
                    0,
                )
            self.assertIn("created", output.getvalue())
            document = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(document["components"][0]["lifecycle"], "on-demand")

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--id", "org.example.stdout", "--runtime", "tk",
                            "--name", "触摸应用", "--output", "-",
                        ]
                    ),
                    0,
                )
            self.assertEqual(json.loads(output.getvalue())["package"]["name"], "触摸应用")

    def test_cli_reports_contract_errors_without_traceback(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--id", "invalid", "--runtime", "python", "--output", "-"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid package id", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
