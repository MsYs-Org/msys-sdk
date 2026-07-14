"""Canonical window identity helpers for lightweight MSYS GUI clients.

Tk normalizes a ``className`` beginning with a lower-case letter by
capitalizing its first character.  That makes a manifest identity such as
``org.msys.settings`` appear as ``Org.msys.settings`` in ``WM_CLASS`` and can
break exact component ownership.  This module repairs the X11 properties on
the actual Tk window without adding an Xlib Python dependency.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass
from typing import Any, Mapping


MAX_IDENTITY_BYTES = 192
_APPLIED_ATTRIBUTE = "_msys_window_identity_applied"


def _clean(value: object, fallback: str) -> str:
    text = str(value or "").strip().replace("\x00", "")
    if not text:
        text = fallback
    return text.encode("utf-8")[:MAX_IDENTITY_BYTES].decode("utf-8", "ignore")


def _canonical_token(value: object, fallback: str) -> str:
    return _clean(value, fallback).casefold()


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    app_id: str
    component_id: str
    role: str
    wm_class: str
    wm_instance: str


def window_identity(
    default_app_id: str,
    *,
    default_role: str = "application",
    default_instance: str = "main",
    environ: Mapping[str, str] | None = None,
) -> WindowIdentity:
    """Resolve one canonical identity from the supervisor environment.

    Manifest application IDs, roles, and X11 class names are case-insensitive
    contract tokens and are normalized to lower case.  The full component ID
    is retained verbatim because it is also the mIPC routing identity.
    """

    values = os.environ if environ is None else environ
    fallback_app = _canonical_token(default_app_id, "unknown")
    app_id = _canonical_token(
        values.get("MSYS_APP_ID") or values.get("MSYS_WINDOW_IDENTITY"),
        fallback_app,
    )
    component_id = _clean(values.get("MSYS_COMPONENT_ID"), app_id)
    role = _canonical_token(values.get("MSYS_WINDOW_ROLE"), default_role)
    wm_class = _canonical_token(values.get("MSYS_WINDOW_IDENTITY"), app_id)
    component_instance = component_id.rpartition(":")[2] if ":" in component_id else ""
    wm_instance = _canonical_token(component_instance, default_instance)
    return WindowIdentity(app_id, component_id, role, wm_class, wm_instance)


class _XClassHint(ctypes.Structure):
    _fields_ = [("res_name", ctypes.c_char_p), ("res_class", ctypes.c_char_p)]


def _load_x11() -> Any:
    name = ctypes.util.find_library("X11") or "libX11.so.6"
    library = ctypes.CDLL(name)
    library.XOpenDisplay.argtypes = [ctypes.c_char_p]
    library.XOpenDisplay.restype = ctypes.c_void_p
    library.XCloseDisplay.argtypes = [ctypes.c_void_p]
    library.XCloseDisplay.restype = ctypes.c_int
    library.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    library.XInternAtom.restype = ctypes.c_ulong
    library.XChangeProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
    ]
    library.XChangeProperty.restype = ctypes.c_int
    library.XSetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XClassHint)]
    library.XSetClassHint.restype = ctypes.c_int
    library.XFlush.argtypes = [ctypes.c_void_p]
    library.XFlush.restype = ctypes.c_int
    return library


def _write_x11_identity(
    window_id: int,
    display_name: str,
    identity: WindowIdentity,
) -> bool:
    try:
        x11 = _load_x11()
        display_bytes = display_name.encode("utf-8") if display_name else None
        display = x11.XOpenDisplay(display_bytes)
        if not display:
            return False
        try:
            instance = identity.wm_instance.encode("utf-8")
            wm_class = identity.wm_class.encode("utf-8")
            hint = _XClassHint(instance, wm_class)
            x11.XSetClassHint(display, int(window_id), ctypes.byref(hint))

            utf8 = x11.XInternAtom(display, b"UTF8_STRING", 0)
            for property_name, value in (
                (b"_MSYS_APP_ID", identity.app_id),
                (b"_MSYS_COMPONENT_ID", identity.component_id),
                (b"_MSYS_WINDOW_ROLE", identity.role),
            ):
                atom = x11.XInternAtom(display, property_name, 0)
                encoded = value.encode("utf-8")
                buffer = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
                x11.XChangeProperty(
                    display,
                    int(window_id),
                    atom,
                    utf8,
                    8,
                    0,  # PropModeReplace
                    buffer,
                    len(encoded),
                )
            x11.XFlush(display)
            return True
        finally:
            x11.XCloseDisplay(display)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def configure_tk_window_identity(
    window: Any,
    default_app_id: str,
    *,
    default_role: str = "application",
    default_instance: str = "main",
    environ: Mapping[str, str] | None = None,
) -> WindowIdentity:
    """Apply canonical ``WM_CLASS`` and ``_MSYS_*`` properties to a Tk window.

    The operation is best-effort so the same application remains runnable on
    Windows and on Tk builds without X11.  Call it for each independently
    managed ``Tk``/``Toplevel`` surface after construction.
    """

    identity = window_identity(
        default_app_id,
        default_role=default_role,
        default_instance=default_instance,
        environ=environ,
    )
    try:
        # ``winfo_id`` materializes the native window without forcing an
        # otherwise not-yet-started root through an event-loop/map cycle.
        # This lets the corrected WM_CLASS exist before the first MapRequest.
        window_id = int(window.winfo_id())
        display_name = str(window.winfo_screen() or "")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return identity
    _write_x11_identity(window_id, display_name, identity)
    try:
        setattr(window, _APPLIED_ATTRIBUTE, identity)
    except (AttributeError, RuntimeError):
        pass
    return identity


__all__ = [
    "WindowIdentity",
    "configure_tk_window_identity",
    "window_identity",
]
