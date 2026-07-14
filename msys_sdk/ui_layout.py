"""Small responsive-layout helpers shared by MSYS graphical applications.

The module imports neither Tk nor Qt at import time.  It standardizes the two
failure-prone basics for fixed and rotating screens: text gets a bounded wrap
width, and long pages live in a vertically scrollable viewport.  Applications
still own their visual style and widget hierarchy.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable


DEFAULT_HORIZONTAL_PADDING = 32
DEFAULT_MINIMUM_WRAP = 96


def content_width(
    width: int,
    *,
    horizontal_padding: int = DEFAULT_HORIZONTAL_PADDING,
    minimum: int = DEFAULT_MINIMUM_WRAP,
    maximum: int | None = None,
) -> int:
    """Return a safe content/wrap width for the current viewport."""

    viewport = max(0, int(width))
    padding = max(0, int(horizontal_padding))
    lower = max(1, int(minimum))
    result = max(lower, viewport - padding)
    if maximum is not None:
        result = min(result, max(lower, int(maximum)))
    return result


def responsive_columns(
    width: int,
    *,
    minimum_item_width: int,
    gap: int = 12,
    maximum: int | None = None,
) -> int:
    """Choose a stable grid column count without framework-specific code."""

    available = max(0, int(width))
    item = max(1, int(minimum_item_width))
    spacing = max(0, int(gap))
    columns = max(1, (available + spacing) // (item + spacing))
    return min(columns, max(1, int(maximum))) if maximum is not None else columns


def bind_tk_text_wrap(
    widget: Any,
    container: Any | None = None,
    *,
    horizontal_padding: int = DEFAULT_HORIZONTAL_PADDING,
    minimum: int = DEFAULT_MINIMUM_WRAP,
    maximum: int | None = None,
) -> Callable[[], None]:
    """Keep a Tk Label/Message ``wraplength`` tied to its viewport width.

    The returned function removes only this binding.  ``add='+'`` preserves
    application bindings, and unsupported/destroyed widgets fail quietly.
    """

    owner = container if container is not None else widget

    def resize(event: Any) -> None:
        try:
            widget.configure(
                wraplength=content_width(
                    event.width,
                    horizontal_padding=horizontal_padding,
                    minimum=minimum,
                    maximum=maximum,
                )
            )
        except (AttributeError, RuntimeError, TypeError):
            pass

    binding_id = owner.bind("<Configure>", resize, add="+")
    try:
        resize(SimpleNamespace(width=owner.winfo_width()))
    except (AttributeError, RuntimeError, TypeError):
        pass

    def unbind() -> None:
        try:
            owner.unbind("<Configure>", binding_id)
        except (AttributeError, RuntimeError, TypeError):
            pass

    return unbind


def _qt_enum(qt_core: Any, group: str, name: str) -> Any | None:
    if qt_core is None:
        return None
    qt = getattr(qt_core, "Qt", None)
    if qt is None:
        return None
    nested = getattr(qt, group, None)
    return getattr(nested, name, None) if nested is not None else getattr(qt, name, None)


def configure_qt_text_wrap(*widgets: Any) -> None:
    """Enable word wrapping and horizontal shrinking for QLabel-like widgets."""

    for widget in widgets:
        try:
            widget.setWordWrap(True)
            widget.setMinimumWidth(0)
        except (AttributeError, RuntimeError, TypeError):
            continue


def configure_qt_scroll_area(
    area: Any,
    content: Any | None = None,
    *,
    qt_core: Any | None = None,
) -> Any:
    """Configure a QScrollArea-like page for responsive vertical content."""

    area.setWidgetResizable(True)
    if content is not None:
        area.setWidget(content)
    horizontal_off = _qt_enum(
        qt_core, "ScrollBarPolicy", "ScrollBarAlwaysOff"
    )
    vertical_auto = _qt_enum(
        qt_core, "ScrollBarPolicy", "ScrollBarAsNeeded"
    )
    if horizontal_off is not None:
        area.setHorizontalScrollBarPolicy(horizontal_off)
    if vertical_auto is not None:
        area.setVerticalScrollBarPolicy(vertical_auto)
    return area


class TkScrollablePage:
    """A compositional Tk/ttk vertical page with touch-drag and wheel support.

    Add page widgets under ``page.content`` and place ``page.widget`` using
    ``pack``/``grid``/``place`` (also proxied by this object).  No global event
    binding is installed.  Call ``bind_touch_scroll(page.content)`` after the
    page is built to bind that subtree; future widgets can be bound explicitly.
    """

    def __init__(
        self,
        parent: Any,
        *,
        background: str | None = None,
        scrollbar: bool = True,
        drag_threshold: int = 8,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.widget = ttk.Frame(parent)
        options: dict[str, object] = {
            "highlightthickness": 0,
            "borderwidth": 0,
            "takefocus": 0,
        }
        if background is not None:
            options["background"] = background
        self.canvas = tk.Canvas(self.widget, **options)
        self.content = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )
        self._scrollbar = ttk.Scrollbar(
            self.widget, orient="vertical", command=self.canvas.yview
        ) if scrollbar else None
        self.canvas.configure(
            yscrollcommand=(self._scrollbar_set if scrollbar else None)
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        if self._scrollbar is not None:
            self._scrollbar.grid(row=0, column=1, sticky="ns")
        self.widget.rowconfigure(0, weight=1)
        self.widget.columnconfigure(0, weight=1)
        self._drag_threshold = max(0, int(drag_threshold))
        self._drag_origin = 0
        self._drag_last = 0
        self._dragging = False
        self.content.bind("<Configure>", self._content_resized, add="+")
        self.canvas.bind("<Configure>", self._canvas_resized, add="+")
        self.bind_touch_scroll(self.canvas, recursive=False)

    def _content_resized(self, _event: Any = None) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except (RuntimeError, TypeError):
            pass

    def _canvas_resized(self, event: Any) -> None:
        try:
            self.canvas.itemconfigure(self._window, width=max(1, int(event.width)))
        except (RuntimeError, TypeError, ValueError):
            pass

    def _scrollbar_set(self, first: str, last: str) -> None:
        if self._scrollbar is None:
            return
        self._scrollbar.set(first, last)
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self._scrollbar.grid_remove()
            else:
                self._scrollbar.grid()
        except (RuntimeError, TypeError, ValueError):
            pass

    def _press(self, event: Any) -> None:
        self._drag_origin = int(event.y_root)
        self._drag_last = self._drag_origin
        self._dragging = False
        try:
            self.canvas.scan_mark(0, self._drag_origin)
        except (RuntimeError, TypeError):
            pass

    def _motion(self, event: Any) -> str | None:
        current = int(event.y_root)
        if not self._dragging and abs(current - self._drag_origin) < self._drag_threshold:
            return None
        self._dragging = True
        self._drag_last = current
        try:
            self.canvas.scan_dragto(0, current, gain=1)
        except (RuntimeError, TypeError):
            pass
        return "break"

    def _release(self, _event: Any) -> str | None:
        dragged = self._dragging
        self._dragging = False
        return "break" if dragged else None

    def _wheel(self, event: Any) -> str:
        delta = getattr(event, "delta", 0)
        if delta:
            units = -1 if delta > 0 else 1
        else:
            units = -1 if getattr(event, "num", 0) == 4 else 1
        self.canvas.yview_scroll(units * 3, "units")
        return "break"

    def bind_touch_scroll(self, widget: Any, *, recursive: bool = True) -> None:
        """Bind drag/wheel scrolling to a page subtree without ``bind_all``."""

        widget.bind("<ButtonPress-1>", self._press, add="+")
        widget.bind("<B1-Motion>", self._motion, add="+")
        widget.bind("<ButtonRelease-1>", self._release, add="+")
        widget.bind("<MouseWheel>", self._wheel, add="+")
        widget.bind("<Button-4>", self._wheel, add="+")
        widget.bind("<Button-5>", self._wheel, add="+")
        if recursive:
            try:
                children = widget.winfo_children()
            except (AttributeError, RuntimeError):
                children = ()
            for child in children:
                self.bind_touch_scroll(child, recursive=True)

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)

    def refresh(self) -> None:
        self._content_resized()

    def pack(self, *args: Any, **kwargs: Any) -> Any:
        return self.widget.pack(*args, **kwargs)

    def grid(self, *args: Any, **kwargs: Any) -> Any:
        return self.widget.grid(*args, **kwargs)

    def place(self, *args: Any, **kwargs: Any) -> Any:
        return self.widget.place(*args, **kwargs)

    def destroy(self) -> None:
        self.widget.destroy()


__all__ = [
    "DEFAULT_HORIZONTAL_PADDING",
    "DEFAULT_MINIMUM_WRAP",
    "TkScrollablePage",
    "bind_tk_text_wrap",
    "configure_qt_scroll_area",
    "configure_qt_text_wrap",
    "content_width",
    "responsive_columns",
]
