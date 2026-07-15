from __future__ import annotations

import types
import unittest
from unittest import mock
from pathlib import Path
import re

from msys_sdk import (
    TkInputMethodBinding,
    __version__,
    bind_tk_input_method,
)
from msys_sdk.ui_input_method import (
    DEFAULT_INPUT_METHOD_FOCUS_SETTLE_MS,
    DEFAULT_INPUT_METHOD_HIDE_TIMEOUT,
    DEFAULT_INPUT_METHOD_SHOW_TIMEOUT,
    INPUT_METHOD_TARGET,
)


class InlineThread:
    def __init__(self, *, target, **_kwargs) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


class FakeWidget:
    def __init__(self, master=None) -> None:
        self.master = master
        self.bindings = {}
        self.unbound = []
        self._next_binding = 1

    def bind(self, sequence, callback, add=None):
        binding_id = f"binding-{self._next_binding}"
        self._next_binding += 1
        self.bindings[sequence] = callback
        return binding_id

    def unbind(self, sequence, binding_id):
        self.unbound.append((sequence, binding_id))


class FakeRoot(FakeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.focused = None
        self.scheduled = {}
        self.cancelled = []
        self._next_after = 1

    def focus_get(self):
        return self.focused

    def after(self, delay, callback):
        handle = f"after-{self._next_after}"
        self._next_after += 1
        self.scheduled[handle] = (delay, callback)
        return handle

    def after_idle(self, callback):
        callback()
        return "idle"

    def after_cancel(self, handle):
        self.cancelled.append(handle)
        self.scheduled.pop(handle, None)

    def run_scheduled(self) -> None:
        scheduled = list(self.scheduled.values())
        self.scheduled.clear()
        for _delay, callback in scheduled:
            callback()


class Editor(FakeWidget):
    def __init__(self, root) -> None:
        super().__init__(root)
        self.root = root

    def winfo_toplevel(self):
        return self.root


class RecordingCall:
    def __init__(self, failures=0) -> None:
        self.calls = []
        self.failures = failures

    def __call__(self, target, method, payload, *, timeout):
        self.calls.append((target, method, payload, timeout))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("unavailable")
        return {"type": "return", "payload": {"ok": True}}


@mock.patch("msys_sdk.ui_input_method.threading.Thread", InlineThread)
class TkInputMethodBindingTests(unittest.TestCase):
    def test_public_api_version_is_consistent(self) -> None:
        project = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = project.read_text(encoding="utf-8")
        self.assertEqual(__version__, "0.1.13")
        self.assertEqual(
            re.search(r'(?m)^version\s*=\s*"([^"]+)"', text).group(1),
            __version__,
        )

    def test_focus_touch_role_addressing_deadline_and_outside_hide(self) -> None:
        root = FakeRoot()
        editor = Editor(root)
        outside = FakeWidget(root)
        role_call = RecordingCall()
        binding = TkInputMethodBinding(root, role_call)
        self.assertIs(binding.bind(editor, mode="zh"), editor)

        editor.bindings["<ButtonPress-1>"](
            types.SimpleNamespace(widget=editor)
        )
        editor.bindings["<FocusIn>"](types.SimpleNamespace(widget=editor))
        self.assertEqual(
            role_call.calls,
            [
                (
                    INPUT_METHOD_TARGET,
                    "show",
                    {"mode": "zh"},
                    DEFAULT_INPUT_METHOD_SHOW_TIMEOUT,
                )
            ],
        )

        # A later real touch reasserts show after a provider-local dismissal;
        # FocusIn from the same original touch did not duplicate the call.
        editor.bindings["<ButtonPress-1>"](
            types.SimpleNamespace(widget=editor)
        )
        self.assertEqual([item[1] for item in role_call.calls], ["show", "show"])
        root.bindings["<ButtonPress-1>"](
            types.SimpleNamespace(widget=outside)
        )
        self.assertEqual(
            role_call.calls[-1],
            (
                INPUT_METHOD_TARGET,
                "hide",
                {},
                DEFAULT_INPUT_METHOD_HIDE_TIMEOUT,
            ),
        )

    def test_focusout_settles_and_focusin_cancels_transient_hide(self) -> None:
        root = FakeRoot()
        editor = Editor(root)
        role_call = RecordingCall()
        binding = TkInputMethodBinding(root, role_call)
        binding.bind(editor)
        root.focused = editor
        editor.bindings["<FocusIn>"](types.SimpleNamespace(widget=editor))

        editor.bindings["<FocusOut>"](types.SimpleNamespace(widget=editor))
        handle = binding._focus_check
        self.assertEqual(
            root.scheduled[handle][0],
            DEFAULT_INPUT_METHOD_FOCUS_SETTLE_MS,
        )
        editor.bindings["<FocusIn>"](types.SimpleNamespace(widget=editor))
        self.assertIn(handle, root.cancelled)
        root.run_scheduled()
        self.assertEqual([item[1] for item in role_call.calls], ["show"])

        root.focused = None
        editor.bindings["<FocusOut>"](types.SimpleNamespace(widget=editor))
        root.run_scheduled()
        self.assertEqual([item[1] for item in role_call.calls], ["show", "hide"])

    def test_already_focused_editor_is_reconciled_after_binding(self) -> None:
        root = FakeRoot()
        editor = Editor(root)
        root.focused = editor
        role_call = RecordingCall()
        binding = TkInputMethodBinding(root, role_call)

        binding.bind(editor, mode="numeric")

        self.assertEqual(
            role_call.calls,
            [
                (
                    INPUT_METHOD_TARGET,
                    "show",
                    {"mode": "numeric"},
                    DEFAULT_INPUT_METHOD_SHOW_TIMEOUT,
                )
            ],
        )

    def test_widget_destroy_hides_and_root_destroy_closes_safely(self) -> None:
        root = FakeRoot()
        editor = Editor(root)
        child = FakeWidget(editor)
        role_call = RecordingCall()
        binding = TkInputMethodBinding(root, role_call)
        binding.bind(editor)
        editor.bindings["<FocusIn>"](types.SimpleNamespace(widget=editor))

        editor.bindings["<Destroy>"](types.SimpleNamespace(widget=child))
        self.assertEqual([item[1] for item in role_call.calls], ["show"])
        editor.bindings["<Destroy>"](types.SimpleNamespace(widget=editor))
        self.assertEqual([item[1] for item in role_call.calls], ["show", "hide"])

        root.bindings["<Destroy>"](types.SimpleNamespace(widget=root))
        self.assertTrue(binding.closed)
        self.assertTrue(root.unbound)
        self.assertTrue(editor.unbound)

    def test_failure_is_observable_and_a_later_touch_retries(self) -> None:
        root = FakeRoot()
        editor = Editor(root)
        role_call = RecordingCall(failures=1)
        errors = []
        binding = TkInputMethodBinding(root, role_call, on_error=errors.append)
        binding.bind(editor)

        editor.bindings["<FocusIn>"](types.SimpleNamespace(widget=editor))
        self.assertIsInstance(binding.last_error, RuntimeError)
        self.assertEqual(len(errors), 1)
        editor.bindings["<ButtonPress-1>"](
            types.SimpleNamespace(widget=editor)
        )
        self.assertIsNone(binding.last_error)
        self.assertEqual([item[1] for item in role_call.calls], ["show", "show"])

    def test_close_orders_hide_and_unbinds_without_starting_hidden_role(self) -> None:
        root = FakeRoot()
        editor = Editor(root)
        role_call = RecordingCall()
        binding = TkInputMethodBinding(root, role_call)
        binding.bind(editor)
        binding.close()
        self.assertEqual(role_call.calls, [])

        second = TkInputMethodBinding(root, role_call)
        second.bind(editor)
        second.show(mode="symbols")
        second.close()
        self.assertEqual(
            [item[1] for item in role_call.calls],
            ["show", "hide"],
        )
        self.assertTrue(second.closed)

    def test_convenience_binding_is_optional_and_validates_mode(self) -> None:
        root = FakeRoot()
        editor = Editor(root)
        role_call = RecordingCall()
        binding = bind_tk_input_method(editor, role_call, mode="en")
        self.assertIs(binding.root, root)
        with self.assertRaisesRegex(ValueError, "mode"):
            binding.bind(FakeWidget(root), mode="emoji")
        binding.close(request_hide=False)


if __name__ == "__main__":
    unittest.main()
