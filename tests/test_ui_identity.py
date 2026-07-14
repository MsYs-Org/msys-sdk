from __future__ import annotations

import unittest
from unittest import mock

from msys_sdk.ui_identity import configure_tk_window_identity, window_identity


class FakeWindow:
    def winfo_id(self) -> int:
        return 0x2A

    def winfo_screen(self) -> str:
        return ":24.0"


class WindowIdentityTests(unittest.TestCase):
    def test_environment_is_resolved_to_canonical_tk_identity(self) -> None:
        identity = window_identity(
            "org.example.fallback",
            environ={
                "MSYS_APP_ID": "Org.MSYS.Settings",
                "MSYS_COMPONENT_ID": "org.msys.settings:main",
                "MSYS_WINDOW_IDENTITY": "Org.MSYS.Settings",
                "MSYS_WINDOW_ROLE": "Application",
            },
        )
        self.assertEqual(identity.app_id, "org.msys.settings")
        self.assertEqual(identity.component_id, "org.msys.settings:main")
        self.assertEqual(identity.role, "application")
        self.assertEqual(identity.wm_class, "org.msys.settings")
        self.assertEqual(identity.wm_instance, "main")

    def test_tk_writer_receives_xid_display_and_all_identity_fields(self) -> None:
        window = FakeWindow()
        with mock.patch("msys_sdk.ui_identity._write_x11_identity", return_value=True) as write:
            identity = configure_tk_window_identity(
                window,
                "org.msys.apps",
                default_role="application",
                environ={
                    "MSYS_COMPONENT_ID": "org.msys.apps:calculator",
                    "MSYS_WINDOW_IDENTITY": "org.msys.apps.calculator",
                    "MSYS_WINDOW_ROLE": "application",
                },
            )
        write.assert_called_once_with(0x2A, ":24.0", identity)
        self.assertEqual(identity.app_id, "org.msys.apps.calculator")
        self.assertEqual(identity.wm_class, "org.msys.apps.calculator")
        self.assertEqual(identity.wm_instance, "calculator")

    def test_missing_x11_window_is_a_nonfatal_noop(self) -> None:
        identity = configure_tk_window_identity(
            object(),
            "org.example.app",
            environ={},
        )
        self.assertEqual(identity.app_id, "org.example.app")


if __name__ == "__main__":
    unittest.main()
