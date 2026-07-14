#!/usr/bin/env python3
"""Real Tk/X11 probe for the canonical MSYS window identity.

Run it inside an X11 test session, for example::

    Xvfb :91 -screen 0 640x480x24 &
    DISPLAY=:91 PYTHONPATH=. python3 tests/probe_tk_identity_x11.py

The probe deliberately uses both ``xprop`` and a small ctypes ``XQueryTree``
call.  This makes it useful when Tk changes the relationship between the
widget window and its window-manager wrapper.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import subprocess
import tkinter as tk

from msys_sdk.ui_identity import configure_tk_window_identity


EXPECTED = "org.msys.identity.probe"


def _xid(value: object) -> int:
    return int(str(value), 0)


def _xprop(window_id: int, property_name: str) -> str:
    result = subprocess.run(
        ["xprop", "-id", hex(window_id), property_name],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parent_xid(display_name: str, window_id: int) -> int:
    x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XQueryTree.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    x11.XQueryTree.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(display_name.encode("utf-8"))
    if not display:
        raise RuntimeError(f"cannot open X display {display_name!r}")
    root = ctypes.c_ulong()
    parent = ctypes.c_ulong()
    children = ctypes.POINTER(ctypes.c_ulong)()
    count = ctypes.c_uint()
    try:
        if not x11.XQueryTree(
            display,
            window_id,
            ctypes.byref(root),
            ctypes.byref(parent),
            ctypes.byref(children),
            ctypes.byref(count),
        ):
            raise RuntimeError(f"XQueryTree failed for {window_id:#x}")
        return int(parent.value)
    finally:
        if children:
            x11.XFree(children)
        x11.XCloseDisplay(display)


def main() -> int:
    root = tk.Tk(className=EXPECTED)
    root.title("MSYS identity probe")
    try:
        root.update_idletasks()
        client_id = int(root.winfo_id())
        frame_id = _xid(root.tk.call("wm", "frame", root._w))
        wrapper_id = _parent_xid(root.winfo_screen(), client_id)
        before = {
            "client": _xprop(client_id, "WM_CLASS"),
            "frame": _xprop(frame_id, "WM_CLASS"),
            "wrapper": _xprop(wrapper_id, "WM_CLASS"),
        }

        configure_tk_window_identity(root, EXPECTED, environ={})
        root.update_idletasks()
        root.update()
        wrapper_after_map = _parent_xid(root.winfo_screen(), client_id)

        after = {
            "client": _xprop(client_id, "WM_CLASS"),
            "frame": _xprop(frame_id, "WM_CLASS"),
            "wrapper": _xprop(wrapper_after_map, "WM_CLASS"),
            "app_id": _xprop(wrapper_after_map, "_MSYS_APP_ID"),
        }
        print(
            json.dumps(
                {
                    "client_id": hex(client_id),
                    "frame_id": hex(frame_id),
                    "wrapper_id": hex(wrapper_id),
                    "wrapper_after_map": hex(wrapper_after_map),
                    "before": before,
                    "after": after,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        expected_class = f'WM_CLASS(STRING) = "main", "{EXPECTED}"'
        if wrapper_after_map != wrapper_id:
            raise AssertionError("Tk recreated or reparented its wrapper during Map")
        if after["wrapper"] != expected_class:
            raise AssertionError(
                f"canonical identity is not on Tk's WM wrapper: {after['wrapper']!r}"
            )
        return 0
    finally:
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
