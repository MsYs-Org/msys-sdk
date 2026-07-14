from __future__ import annotations

import unittest
import ctypes
from unittest import mock

from msys_sdk import ui_identity as identity_module
from msys_sdk.ui_identity import configure_tk_window_identity, window_identity


class FakeWindow:
    def __init__(self) -> None:
        self.flushed = False

    def winfo_id(self) -> int:
        return 0x2A

    def update_idletasks(self) -> None:
        self.flushed = True

    def winfo_screen(self) -> str:
        if not self.flushed:
            raise AssertionError("Tk XCreateWindow was not flushed")
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

    def test_successful_identity_is_not_written_twice(self) -> None:
        window = FakeWindow()
        with mock.patch("msys_sdk.ui_identity._write_x11_identity", return_value=True) as write:
            first = configure_tk_window_identity(window, "org.example.app", environ={})
            second = configure_tk_window_identity(window, "org.example.app", environ={})
        self.assertEqual(first, second)
        write.assert_called_once()

    def test_failed_identity_write_remains_retryable(self) -> None:
        window = FakeWindow()
        with mock.patch(
            "msys_sdk.ui_identity._write_x11_identity",
            side_effect=(False, True),
        ) as write:
            configure_tk_window_identity(window, "org.example.app", environ={})
            configure_tk_window_identity(window, "org.example.app", environ={})
        self.assertEqual(write.call_count, 2)

    def test_x11_writer_targets_tk_wrapper_instead_of_content_window(self) -> None:
        class WrapperX11:
            def __init__(self) -> None:
                self.handler = ctypes.c_void_p()
                self.class_targets: list[int] = []
                self.property_targets: list[int] = []

            def XOpenDisplay(self, _name: object) -> int:
                return 7

            def XSetErrorHandler(self, handler: object) -> object:
                previous = self.handler
                self.handler = handler
                return previous

            def XQueryTree(
                self,
                _display: object,
                _window: object,
                root: object,
                parent: object,
                _children: object,
                count: object,
            ) -> int:
                ctypes.cast(root, ctypes.POINTER(ctypes.c_ulong))[0] = 0x01
                ctypes.cast(parent, ctypes.POINTER(ctypes.c_ulong))[0] = 0x2B
                ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = 0
                return 1

            def XFree(self, *_args: object) -> int:
                return 0

            def XSetClassHint(self, _display: object, target: int, _hint: object) -> int:
                self.class_targets.append(target)
                return 1

            def XInternAtom(self, *_args: object) -> int:
                return 1

            def XChangeProperty(
                self,
                _display: object,
                target: int,
                *_args: object,
            ) -> int:
                self.property_targets.append(target)
                return 1

            def XFlush(self, *_args: object) -> int:
                return 0

            def XSync(self, *_args: object) -> int:
                return 0

            def XCloseDisplay(self, *_args: object) -> int:
                return 0

        x11 = WrapperX11()
        identity = window_identity("org.example.app", environ={})
        with mock.patch("msys_sdk.ui_identity._load_x11", return_value=x11):
            applied = identity_module._write_x11_identity(0x2A, ":24.0", identity)
        self.assertTrue(applied)
        self.assertEqual(x11.class_targets, [0x2B])
        self.assertEqual(x11.property_targets, [0x2B, 0x2B, 0x2B])

    def test_async_badwindow_is_nonfatal_and_restores_xlib_handler(self) -> None:
        class BadWindowX11:
            def __init__(self) -> None:
                self.handler = ctypes.c_void_p(0x1234)
                self.handler_changes: list[object] = []
                self.closed = False

            def XOpenDisplay(self, _name: object) -> int:
                return 7

            def XSetErrorHandler(self, handler: object) -> object:
                previous = self.handler
                self.handler = handler
                self.handler_changes.append(handler)
                return previous

            def XSetClassHint(self, *_args: object) -> int:
                return 1

            def XQueryTree(
                self,
                _display: object,
                _window: object,
                root: object,
                parent: object,
                _children: object,
                count: object,
            ) -> int:
                ctypes.cast(root, ctypes.POINTER(ctypes.c_ulong))[0] = 0x01
                ctypes.cast(parent, ctypes.POINTER(ctypes.c_ulong))[0] = 0x2B
                ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = 0
                return 1

            def XFree(self, *_args: object) -> int:
                return 0

            def XInternAtom(self, *_args: object) -> int:
                return 1

            def XChangeProperty(self, *_args: object) -> int:
                return 1

            def XFlush(self, *_args: object) -> int:
                return 0

            def XSync(self, *_args: object) -> int:
                callback = ctypes.cast(self.handler, identity_module._XErrorHandler)
                callback(None, None)
                return 0

            def XCloseDisplay(self, *_args: object) -> int:
                self.closed = True
                return 0

        x11 = BadWindowX11()
        identity = window_identity("org.example.app", environ={})
        with mock.patch("msys_sdk.ui_identity._load_x11", return_value=x11):
            applied = identity_module._write_x11_identity(0x2A, ":24.0", identity)
        self.assertFalse(applied)
        self.assertTrue(x11.closed)
        self.assertEqual(len(x11.handler_changes), 2)
        self.assertEqual(x11.handler_changes[-1].value, 0x1234)

    def test_missing_x11_window_is_a_nonfatal_noop(self) -> None:
        identity = configure_tk_window_identity(
            object(),
            "org.example.app",
            environ={},
        )
        self.assertEqual(identity.app_id, "org.example.app")


if __name__ == "__main__":
    unittest.main()
