"""Optional Tk binding for the replaceable MSYS input-method role.

The module is toolkit-lazy: importing :mod:`msys_sdk` does not import Tk or
start a client.  Applications opt in by passing an already-authorized mIPC
``call`` function.  Qt, Electron, native X11, and other frameworks remain
unaffected and can use the same role contract directly.
"""

from __future__ import annotations

import threading
from typing import Any, Callable


INPUT_METHOD_TARGET = "role:input-method"
INPUT_METHOD_MODES = frozenset({"en", "zh", "numeric", "symbols"})
DEFAULT_INPUT_METHOD_SHOW_TIMEOUT = 6.0
DEFAULT_INPUT_METHOD_HIDE_TIMEOUT = 2.0
DEFAULT_INPUT_METHOD_FOCUS_SETTLE_MS = 80

RoleCall = Callable[..., object]
ErrorCallback = Callable[[Exception], None]


class TkInputMethodBinding:
    """Bind Tk editable widgets to ``role:input-method`` lifecycle calls.

    ``role_call`` normally is ``MsysClient.call`` or a compatible private
    component-channel method.  It must accept ``target, method, payload`` and
    a keyword ``timeout``.  All calls run on one tiny worker, never Tk's event
    thread.  The application still owns client setup and must declare
    ``mipc.call:role:input-method`` in its component permissions.
    """

    def __init__(
        self,
        root: Any,
        role_call: RoleCall,
        *,
        show_timeout: float = DEFAULT_INPUT_METHOD_SHOW_TIMEOUT,
        hide_timeout: float = DEFAULT_INPUT_METHOD_HIDE_TIMEOUT,
        focus_settle_ms: int = DEFAULT_INPUT_METHOD_FOCUS_SETTLE_MS,
        on_error: ErrorCallback | None = None,
    ) -> None:
        if not callable(role_call):
            raise TypeError("role_call must be callable")
        if show_timeout <= 0 or hide_timeout <= 0:
            raise ValueError("input-method timeouts must be positive")
        if (
            isinstance(focus_settle_ms, bool)
            or not isinstance(focus_settle_ms, int)
            or focus_settle_ms < 0
        ):
            raise ValueError("focus_settle_ms must be a non-negative integer")
        if on_error is not None and not callable(on_error):
            raise TypeError("on_error must be callable")

        self.root = root
        self.role_call = role_call
        self.show_timeout = float(show_timeout)
        self.hide_timeout = float(hide_timeout)
        self.focus_settle_ms = int(focus_settle_ms)
        self.on_error = on_error
        self._widgets: dict[Any, str] = {}
        self._bindings: list[tuple[Any, str, object]] = []
        self._lock = threading.Lock()
        self._pending: tuple[bool, str] | None = None
        self._desired: tuple[bool, str] | None = None
        self._worker_running = False
        self._focus_check: object | None = None
        self._closed = False
        self._last_error: Exception | None = None
        self._bind(root, "<ButtonPress-1>", self._root_pointer_press)
        self._bind(root, "<Destroy>", self._root_destroyed)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    def _bind(self, widget: Any, sequence: str, callback: Callable[..., Any]) -> None:
        binding_id = widget.bind(sequence, callback, add="+")
        self._bindings.append((widget, sequence, binding_id))

    @staticmethod
    def _inside(widget: Any, ancestor: Any) -> bool:
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            current = getattr(current, "master", None)
        return False

    def _registration(self, widget: Any) -> tuple[Any, str] | None:
        for editor, mode in tuple(self._widgets.items()):
            if self._inside(widget, editor):
                return editor, mode
        return None

    def bind(self, widget: Any, *, mode: str = "en") -> Any:
        """Attach one editable widget and return it for fluent construction."""

        selected = str(mode).strip().lower()
        if selected not in INPUT_METHOD_MODES:
            raise ValueError("mode must be en, zh, numeric, or symbols")
        if self._closed:
            raise RuntimeError("input-method binding is closed")
        if widget in self._widgets:
            self._widgets[widget] = selected
            self.sync_focus()
            return widget
        self._widgets[widget] = selected

        def focused(_event: object = None) -> None:
            self._cancel_focus_check()
            self._queue(True, self._widgets.get(widget, selected))

        def touched(_event: object = None) -> None:
            self._cancel_focus_check()
            # The provider may have hidden itself after an outside press while
            # Tk focus remained in this editor.  A later physical touch must
            # therefore be allowed to reassert show.
            self._queue(
                True,
                self._widgets.get(widget, selected),
                force=True,
            )

        def focus_lost(_event: object = None) -> None:
            self._schedule_focus_check()

        def destroyed(event: Any) -> None:
            if getattr(event, "widget", None) is not widget:
                return
            self._widgets.pop(widget, None)
            self.hide()

        self._bind(widget, "<FocusIn>", focused)
        self._bind(widget, "<FocusOut>", focus_lost)
        self._bind(widget, "<ButtonPress-1>", touched)
        self._bind(widget, "<Destroy>", destroyed)
        try:
            self.root.after_idle(self.sync_focus)
        except Exception:
            pass
        return widget

    def _root_pointer_press(self, event: Any) -> None:
        if self._registration(getattr(event, "widget", None)) is None:
            self.hide()

    def _root_destroyed(self, event: Any) -> None:
        if getattr(event, "widget", None) is self.root:
            self.close()

    def _cancel_focus_check(self) -> None:
        pending = self._focus_check
        self._focus_check = None
        if pending is None:
            return
        try:
            self.root.after_cancel(pending)
        except Exception:
            pass

    def _schedule_focus_check(self) -> None:
        if self._closed:
            return
        self._cancel_focus_check()

        def check() -> None:
            self._focus_check = None
            self.sync_focus(hide_if_missing=True)

        try:
            self._focus_check = self.root.after(self.focus_settle_ms, check)
        except Exception:
            self._focus_check = None

    def sync_focus(self, *, hide_if_missing: bool = False) -> None:
        """Reconcile focus after client readiness or application activation."""

        if self._closed:
            return
        try:
            focused = self.root.focus_get()
        except Exception:
            return
        registration = self._registration(focused)
        if registration is not None:
            _widget, mode = registration
            self._queue(True, mode)
        elif hide_if_missing:
            self.hide()

    def show(self, *, mode: str = "en", force: bool = False) -> None:
        """Request the selected role explicitly, without naming a provider."""

        selected = str(mode).strip().lower()
        if selected not in INPUT_METHOD_MODES:
            raise ValueError("mode must be en, zh, numeric, or symbols")
        self._cancel_focus_check()
        self._queue(True, selected, force=force)

    def hide(self) -> None:
        """Request hide; repeated pending/settled hides are coalesced."""

        self._cancel_focus_check()
        mode = self._desired[1] if self._desired is not None else "en"
        self._queue(False, mode)

    def _queue(self, visible: bool, mode: str, *, force: bool = False) -> None:
        if self._closed:
            return
        request = (bool(visible), str(mode))
        with self._lock:
            if self._desired == request:
                # FocusIn and ButtonPress can describe one tap.  Force only
                # reasserts after the preceding request has fully completed.
                if not force or self._pending is not None or self._worker_running:
                    return
            self._desired = request
            self._pending = request
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(
            target=self._worker,
            name="msys-tk-input-method",
            daemon=True,
        ).start()

    def _worker(self) -> None:
        while True:
            with self._lock:
                request = self._pending
                self._pending = None
                if request is None or self._closed:
                    self._worker_running = False
                    return
            visible, mode = request
            try:
                self.role_call(
                    INPUT_METHOD_TARGET,
                    "show" if visible else "hide",
                    {"mode": mode} if visible else {},
                    timeout=self.show_timeout if visible else self.hide_timeout,
                )
            except Exception as exc:
                with self._lock:
                    self._last_error = exc
                    if self._desired == request and self._pending is None:
                        self._desired = None
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass
            else:
                with self._lock:
                    self._last_error = None

    def close(self, *, request_hide: bool = True) -> None:
        """Release bindings and best-effort hide before root destruction."""

        if self._closed:
            return
        self._cancel_focus_check()
        with self._lock:
            desired = self._desired
            self._pending = None
            self._desired = (False, desired[1] if desired else "en")
            self._closed = True
        if request_hide and desired is not None and desired[0]:
            try:
                self.role_call(
                    INPUT_METHOD_TARGET,
                    "hide",
                    {},
                    timeout=self.hide_timeout,
                )
            except Exception as exc:
                with self._lock:
                    self._last_error = exc
                if self.on_error is not None:
                    try:
                        self.on_error(exc)
                    except Exception:
                        pass
        self._widgets.clear()
        for widget, sequence, binding_id in reversed(self._bindings):
            try:
                widget.unbind(sequence, binding_id)
            except Exception:
                pass
        self._bindings.clear()


def bind_tk_input_method(
    widget: Any,
    role_call: RoleCall,
    *,
    root: Any | None = None,
    mode: str = "en",
    show_timeout: float = DEFAULT_INPUT_METHOD_SHOW_TIMEOUT,
    hide_timeout: float = DEFAULT_INPUT_METHOD_HIDE_TIMEOUT,
    focus_settle_ms: int = DEFAULT_INPUT_METHOD_FOCUS_SETTLE_MS,
    on_error: ErrorCallback | None = None,
) -> TkInputMethodBinding:
    """Convenience wrapper for one Tk editor.

    Keep the returned object alive and call ``close()`` before destroying the
    root.  Applications with several editors should create one
    :class:`TkInputMethodBinding` and call ``bind`` for each widget.
    """

    owner = root
    if owner is None:
        try:
            owner = widget.winfo_toplevel()
        except Exception as exc:
            raise ValueError("root is required for a non-Tk-like widget") from exc
    binding = TkInputMethodBinding(
        owner,
        role_call,
        show_timeout=show_timeout,
        hide_timeout=hide_timeout,
        focus_settle_ms=focus_settle_ms,
        on_error=on_error,
    )
    binding.bind(widget, mode=mode)
    return binding


__all__ = [
    "DEFAULT_INPUT_METHOD_FOCUS_SETTLE_MS",
    "DEFAULT_INPUT_METHOD_HIDE_TIMEOUT",
    "DEFAULT_INPUT_METHOD_SHOW_TIMEOUT",
    "INPUT_METHOD_MODES",
    "INPUT_METHOD_TARGET",
    "TkInputMethodBinding",
    "bind_tk_input_method",
]
