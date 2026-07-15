"""Responsive Tk application shell and msysd lifecycle integration."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Iterable

from .component_ipc import ComponentChannel, MipcError
from .ui_layout import (
    TkScrollablePage,
    bind_tk_text_wrap,
    responsive_columns,
)
from .ui_identity import configure_tk_window_identity
from .ui_fonts import configure_tk_fonts, font_spec


# Keep ordinary apps visually aligned with the light Material-like Settings UI.
BG = "#f3f6fb"
PANEL = "#ffffff"
SURFACE = "#e8eef8"
FIELD = "#f7f9fc"
TEXT = "#1b1d21"
MUTED = "#626a76"
ACCENT = "#4263eb"
ACCENT_HOVER = "#3451c7"
ACCENT_CONTAINER = "#dfe6ff"
OUTLINE = "#d5dce8"
ERROR = "#ba1a1a"
SUCCESS = "#137a4b"
CARD_MINIMUM_WIDTH = 210
CARD_MAXIMUM_COLUMNS = 2
INPUT_METHOD_SHOW_TIMEOUT = 6.0
INPUT_METHOD_HIDE_TIMEOUT = 2.0
INPUT_METHOD_FOCUS_SETTLE_MS = 80


class _FallbackI18n:
    """Small recovery translator used when an app does not supply a catalog."""

    _MESSAGES = {
        "common.ready": "Ready",
        "common.standalone": "Standalone",
        "common.connection_failed": "Unable to connect to MSYS services",
    }

    def __call__(self, key: str, _params: object = None) -> str:
        return self._MESSAGES.get(key, key)


class ResponsiveCardGrid(ttk.Frame):
    """Reflow cards between one and two columns as the viewport rotates."""

    def __init__(
        self,
        parent: Any,
        *,
        minimum_item_width: int = CARD_MINIMUM_WIDTH,
        maximum_columns: int = CARD_MAXIMUM_COLUMNS,
        gap: int = 8,
    ) -> None:
        super().__init__(parent)
        self.minimum_item_width = max(120, int(minimum_item_width))
        self.maximum_columns = max(1, int(maximum_columns))
        self.gap = max(0, int(gap))
        self._items: list[Any] = []
        self._columns = 0
        self.bind("<Configure>", self._layout, add="+")

    def add(self, widget: Any) -> None:
        self._items.append(widget)
        self._layout()

    def clear(self) -> None:
        for widget in self._items:
            try:
                widget.destroy()
            except tk.TclError:
                pass
        self._items.clear()
        self._layout()

    def _layout(self, event: Any = None) -> None:
        width = int(getattr(event, "width", 0) or self.winfo_width() or 1)
        columns = responsive_columns(
            width,
            minimum_item_width=self.minimum_item_width,
            gap=self.gap,
            maximum=self.maximum_columns,
        )
        for column in range(max(self._columns, columns)):
            self.columnconfigure(column, weight=1 if column < columns else 0)
        self._columns = columns
        half = self.gap // 2
        for index, widget in enumerate(self._items):
            row, column = divmod(index, columns)
            widget.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else half, 0 if column == columns - 1 else half),
                pady=(0, self.gap),
            )


class TouchApplication:
    def __init__(
        self,
        *,
        title: str,
        identity: str,
        icon_name: str | None = None,
        icon_dir: str | os.PathLike[str] | None = None,
        i18n: Callable[..., str] | None = None,
    ) -> None:
        self.i18n = i18n or _FallbackI18n()
        self.icon_dir = Path(icon_dir) if icon_dir is not None else None
        class_name = os.environ.get("MSYS_WINDOW_IDENTITY", identity)
        self.root = tk.Tk(className=class_name)
        # The application owns its visible, localized title.  The manifest
        # title injected as MSYS_WINDOW_TITLE remains a policy fallback, but
        # must not force an English caption after a locale change.
        self.root.title(title)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.compact = self.root.winfo_screenwidth() < 600
        configure_tk_fonts(
            self.root,
            default_size=9 if self.compact else 10,
        )
        # Tk capitalizes a lower-case className before publishing WM_CLASS.
        # Apply the SDK's canonical X11 properties explicitly, including for
        # standalone previews where supervisor identity variables are absent.
        self.window_identity = configure_tk_window_identity(
            self.root,
            identity,
            default_instance=identity.rpartition(".")[2] or "main",
        )
        self.closed = False
        self.status = tk.StringVar(value=self.i18n("common.ready"))
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._channel: ComponentChannel | None = None
        self._status_label: ttk.Label | None = None
        self._images: list[tk.PhotoImage] = []
        self._wrap_bindings: list[Any] = []
        self._scroll_pages: list[TkScrollablePage] = []
        self._input_widgets: dict[Any, str] = {}
        self._input_root_bound = False
        self._input_lock = threading.Lock()
        self._input_pending: tuple[bool, str] | None = None
        self._input_desired: tuple[bool, str] | None = None
        self._input_worker_running = False
        self._input_focus_check: str | None = None
        self._configure_style()
        self._size_window()
        self._window_icon = self._load_icon(icon_name)
        if self._window_icon is not None:
            try:
                self.root.iconphoto(True, self._window_icon)
            except tk.TclError:
                pass
        self.root.after(50, self._poll_messages)

    def _load_icon(self, icon_name: str | None) -> tk.PhotoImage | None:
        if not icon_name:
            return None
        if self.icon_dir is None:
            return None
        path = self.icon_dir / icon_name
        try:
            image = tk.PhotoImage(master=self.root, file=str(path))
        except (OSError, tk.TclError):
            return None
        self._images.append(image)
        return image

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.option_add("*Font", "TkDefaultFont")
        style.configure("TFrame", background=BG)
        style.configure("Header.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL, relief="flat")
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Header.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure(
            "Status.TLabel",
            background=PANEL,
            foreground=MUTED,
            padding=(0, 3),
        )
        style.configure(
            "StatusError.TLabel",
            background=PANEL,
            foreground=ERROR,
            padding=(0, 3),
        )
        style.configure(
            "Title.TLabel",
            background=PANEL,
            foreground=TEXT,
            font=font_spec(self.root, 14 if self.compact else 18, "bold"),
        )
        style.configure(
            "CardTitle.TLabel",
            background=PANEL,
            foreground=TEXT,
            font=font_spec(self.root, 10 if self.compact else 12, "bold"),
        )
        style.configure(
            "Success.TLabel",
            background=ACCENT_CONTAINER,
            foreground=SUCCESS,
            padding=(6, 2),
        )
        style.configure(
            "Error.TLabel",
            background="#ffdad6",
            foreground=ERROR,
            padding=(6, 2),
        )
        style.configure(
            "TButton",
            padding=((9, 7) if self.compact else (12, 8)),
            background=SURFACE,
            foreground=TEXT,
            relief="flat",
            borderwidth=0,
            focuscolor=ACCENT,
        )
        style.map(
            "TButton",
            background=[("pressed", ACCENT_HOVER), ("active", ACCENT_CONTAINER)],
            foreground=[("pressed", "#ffffff"), ("disabled", "#a7afbb")],
        )
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#ffffff",
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Accent.TButton",
            background=[("pressed", "#293d94"), ("active", ACCENT_HOVER)],
        )
        style.configure(
            "TEntry",
            fieldbackground=FIELD,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=OUTLINE,
            padding=7,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=SURFACE,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            width=14 if self.compact else 16,
            arrowsize=13 if self.compact else 15,
        )

    def _size_window(self) -> None:
        width = min(max(1, self.root.winfo_screenwidth()), 960)
        height = min(max(1, self.root.winfo_screenheight()), 760)
        self.root.geometry(f"{width}x{height}+0+0")

    def header(self, title: str) -> ttk.Frame:
        frame = ttk.Frame(self.root, style="Header.TFrame", padding=(10, 7, 10, 5))
        frame.pack(fill="x")
        top = ttk.Frame(frame, style="Header.TFrame")
        top.pack(fill="x")
        actions = ttk.Frame(top, style="Header.TFrame")
        actions.pack(side="right", padx=(6, 0))
        if self._window_icon is not None:
            ttk.Label(
                top,
                image=self._window_icon,
                style="Header.TLabel",
            ).pack(side="left", padx=(0, 8))
        title_label = ttk.Label(
            top,
            text=title,
            style="Title.TLabel",
            anchor="w",
            justify="left",
        )
        title_label.pack(side="left", fill="x", expand=True)
        self._wrap_bindings.append(
            bind_tk_text_wrap(
                title_label,
                top,
                horizontal_padding=150 if self.compact else 260,
                minimum=70,
                maximum=480,
            )
        )
        self._status_label = ttk.Label(
            frame,
            textvariable=self.status,
            style="Status.TLabel",
            anchor="w",
            justify="left",
        )
        self._status_label.pack(fill="x", pady=(2, 0))
        self._wrap_bindings.append(
            bind_tk_text_wrap(
                self._status_label,
                frame,
                horizontal_padding=4,
                minimum=120,
                maximum=760,
            )
        )
        return actions

    def scroll_page(
        self,
        parent: Any | None = None,
        *,
        padding: tuple[int, int, int, int] = (9, 8, 9, 9),
    ) -> TkScrollablePage:
        page = TkScrollablePage(parent or self.root, background=BG, scrollbar=True)
        page.content.configure(padding=padding)
        page.pack(fill="both", expand=True)
        self._scroll_pages.append(page)
        return page

    def bind_wrap(
        self,
        widget: Any,
        container: Any,
        *,
        horizontal_padding: int = 24,
        minimum: int = 90,
        maximum: int = 620,
    ) -> None:
        unbind = bind_tk_text_wrap(
            widget,
            container,
            horizontal_padding=horizontal_padding,
            minimum=minimum,
            maximum=maximum,
        )
        self._wrap_bindings.append(unbind)

        def release(event: Any) -> None:
            if getattr(event, "widget", None) is not widget:
                return
            try:
                unbind()
            except (RuntimeError, tk.TclError):
                pass
            try:
                self._wrap_bindings.remove(unbind)
            except ValueError:
                pass

        widget.bind("<Destroy>", release, add="+")

    def card_grid(self, parent: Any) -> ResponsiveCardGrid:
        grid = ResponsiveCardGrid(
            parent,
            minimum_item_width=CARD_MINIMUM_WIDTH,
            maximum_columns=CARD_MAXIMUM_COLUMNS,
        )
        grid.pack(fill="both", expand=True)
        return grid

    def bind_touch_text_scroll(
        self,
        widget: Any,
        *,
        drag_threshold: int = 8,
    ) -> None:
        """Give a Text-like editor direct finger scrolling without bind_all.

        A tap keeps Tk's normal cursor/selection behavior.  Once movement
        crosses the small threshold the gesture becomes a vertical scan and
        no longer extends a text selection.  Mouse-wheel support stays scoped
        to the editor so another application cannot inherit the binding.
        """

        threshold = max(0, int(drag_threshold))
        origin = 0
        dragging = False

        def press(event: Any) -> None:
            nonlocal origin, dragging
            origin = int(getattr(event, "y", 0))
            dragging = False
            try:
                widget.scan_mark(0, origin)
            except (AttributeError, RuntimeError, tk.TclError):
                pass

        def motion(event: Any) -> str | None:
            nonlocal dragging
            current = int(getattr(event, "y", origin))
            if not dragging and abs(current - origin) < threshold:
                return None
            dragging = True
            try:
                # tkinter.Text.scan_dragto on Debian 11 accepts only x/y;
                # unlike Canvas it has no public ``gain`` keyword.
                widget.scan_dragto(0, current)
            except (AttributeError, RuntimeError, tk.TclError):
                pass
            return "break"

        def release(_event: Any) -> str | None:
            nonlocal dragging
            dragged = dragging
            dragging = False
            return "break" if dragged else None

        def wheel(event: Any) -> str:
            delta = int(getattr(event, "delta", 0) or 0)
            units = (-1 if delta > 0 else 1) if delta else (
                -1 if getattr(event, "num", 0) == 4 else 1
            )
            try:
                widget.yview_scroll(units * 3, "units")
            except (AttributeError, RuntimeError, tk.TclError):
                pass
            return "break"

        widget.bind("<ButtonPress-1>", press, add="+")
        widget.bind("<B1-Motion>", motion, add="+")
        widget.bind("<ButtonRelease-1>", release, add="+")
        widget.bind("<MouseWheel>", wheel, add="+")
        widget.bind("<Button-4>", wheel, add="+")
        widget.bind("<Button-5>", wheel, add="+")

    def info_card(
        self,
        parent: Any,
        *,
        title: str,
        items: Iterable[tuple[str, str]],
        available: bool = True,
        status: str = "",
    ) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        heading = ttk.Frame(card, style="Card.TFrame")
        heading.pack(fill="x", pady=(0, 5))
        title_label = ttk.Label(
            heading,
            text=title,
            style="CardTitle.TLabel",
            anchor="w",
            justify="left",
        )
        title_label.pack(side="left", fill="x", expand=True)
        if status:
            ttk.Label(
                heading,
                text=status,
                style="Success.TLabel" if available else "Error.TLabel",
            ).pack(side="right", padx=(6, 0))
        self.bind_wrap(
            title_label,
            card,
            horizontal_padding=100 if status else 24,
            minimum=72,
            maximum=540,
        )
        rows = list(items)
        for index, (primary, secondary) in enumerate(rows):
            if index:
                ttk.Separator(card, orient="horizontal").pack(fill="x", pady=6)
            primary_label = ttk.Label(
                card,
                text=str(primary),
                style="Card.TLabel",
                anchor="w",
                justify="left",
            )
            primary_label.pack(fill="x")
            self.bind_wrap(primary_label, card)
            if secondary:
                secondary_label = ttk.Label(
                    card,
                    text=str(secondary),
                    style="CardMuted.TLabel",
                    anchor="w",
                    justify="left",
                )
                secondary_label.pack(fill="x", pady=(2, 0))
                self.bind_wrap(secondary_label, card)
        return card

    def set_status(self, message: str, *, error: bool = False) -> None:
        value = str(message).strip() or self.i18n("common.ready")
        self.status.set(value)
        if self._status_label is not None:
            self._status_label.configure(
                style="StatusError.TLabel" if error else "Status.TLabel"
            )

    def activate_lifecycle(self) -> ComponentChannel | None:
        try:
            self._channel = ComponentChannel.from_environment()
            if self._channel is None:
                self.set_status(self.i18n("common.standalone"))
                return None
            self._channel.handshake()
        except MipcError:
            self.set_status(self.i18n("common.connection_failed"), error=True)
            return None
        self._channel.start(lambda message: self.post("lifecycle", message))
        # Notes selects its editor before the component handshake.  Reconcile
        # that already-valid focus once the private channel can actually route
        # role calls; otherwise the first FocusIn is permanently lost.
        self.root.after_idle(self._reconcile_input_method_focus)
        return self._channel

    @staticmethod
    def _inside(widget: Any, ancestor: Any) -> bool:
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            current = getattr(current, "master", None)
        return False

    def _input_registration(self, widget: Any) -> tuple[Any, str] | None:
        for editor, mode in tuple(self._input_widgets.items()):
            if self._inside(widget, editor):
                return editor, mode
        return None

    def attach_input_method(self, widget: Any, *, mode: str = "en") -> None:
        """Attach the replaceable IME with ordered show/hide requests.

        Focus events can arrive faster than an mIPC round trip.  One tiny
        coalescing worker preserves request order, while a click on any
        non-editor app control hides the overlay immediately and a destroyed
        editor cannot leave it stranded.
        """

        selected_mode = str(mode).strip().lower()
        if selected_mode not in {"en", "zh", "numeric", "symbols"}:
            raise ValueError("unsupported input method mode")
        self._input_widgets[widget] = selected_mode

        def show(_event: object = None) -> None:
            self._cancel_input_focus_check()
            self._queue_input_method(True, selected_mode)

        def touched(_event: object = None) -> None:
            self._cancel_input_focus_check()
            # The provider can dismiss itself after an outside press while the
            # app still owns focus.  A real new touch therefore reasserts show
            # even when the last requested state was already visible.
            self._queue_input_method(True, selected_mode, force=True)

        def focus_out(_event: object = None) -> None:
            self._schedule_input_focus_check()

        def destroyed(event: Any) -> None:
            if getattr(event, "widget", None) is not widget:
                return
            self._input_widgets.pop(widget, None)
            self._queue_input_method(False, selected_mode)

        widget.bind("<FocusIn>", show, add="+")
        widget.bind("<FocusOut>", focus_out, add="+")
        widget.bind("<ButtonPress-1>", touched, add="+")
        widget.bind("<Destroy>", destroyed, add="+")
        if not self._input_root_bound:
            self.root.bind("<ButtonPress-1>", self._input_pointer_press, add="+")
            self._input_root_bound = True

    def _input_pointer_press(self, event: Any) -> None:
        registration = self._input_registration(getattr(event, "widget", None))
        if registration is None:
            self.request_input_method_hide()

    def _cancel_input_focus_check(self) -> None:
        pending = self._input_focus_check
        self._input_focus_check = None
        if pending is None:
            return
        try:
            self.root.after_cancel(pending)
        except (RuntimeError, tk.TclError):
            pass

    def _schedule_input_focus_check(self) -> None:
        self._cancel_input_focus_check()

        def check() -> None:
            self._input_focus_check = None
            self._reconcile_input_method_focus(hide_if_missing=True)

        try:
            self._input_focus_check = self.root.after(
                INPUT_METHOD_FOCUS_SETTLE_MS,
                check,
            )
        except (RuntimeError, tk.TclError):
            self._input_focus_check = None

    def _reconcile_input_method_focus(self, *, hide_if_missing: bool = False) -> None:
        if self.closed or self._channel is None:
            return
        try:
            focused = self.root.focus_get()
        except (RuntimeError, tk.TclError):
            return
        registration = self._input_registration(focused)
        if registration is not None:
            _widget, mode = registration
            self._queue_input_method(True, mode)
        elif hide_if_missing:
            self.request_input_method_hide()

    def request_input_method_hide(self) -> None:
        """Hide the selected role without naming its implementation."""

        self._cancel_input_focus_check()
        current_mode = self._input_desired[1] if self._input_desired else "en"
        self._queue_input_method(False, current_mode)

    def _queue_input_method(
        self,
        visible: bool,
        mode: str,
        *,
        force: bool = False,
    ) -> None:
        if self.closed or self._channel is None:
            return
        request = (bool(visible), str(mode))
        with self._input_lock:
            if self._input_desired == request:
                # FocusIn and ButtonPress can describe the same physical tap.
                # Never duplicate an in-flight request; force is only for a
                # later touch after the provider may have dismissed itself.
                if (
                    not force
                    or self._input_pending is not None
                    or self._input_worker_running
                ):
                    return
            self._input_desired = request
            self._input_pending = request
            if self._input_worker_running:
                return
            self._input_worker_running = True
        threading.Thread(
            target=self._input_method_worker,
            name="msys-app-input-method",
            daemon=True,
        ).start()

    def _input_method_worker(self) -> None:
        while True:
            with self._input_lock:
                request = self._input_pending
                self._input_pending = None
                if request is None or self.closed:
                    self._input_worker_running = False
                    return
            visible, mode = request
            channel = self._channel
            if channel is None:
                with self._input_lock:
                    self._input_desired = None
                continue
            try:
                channel.call(
                    "role:input-method",
                    "show" if visible else "hide",
                    {"mode": mode} if visible else {},
                    timeout=(
                        INPUT_METHOD_SHOW_TIMEOUT
                        if visible
                        else INPUT_METHOD_HIDE_TIMEOUT
                    ),
                )
            except MipcError:
                with self._input_lock:
                    if self._input_desired == request and self._input_pending is None:
                        self._input_desired = None

    def post(self, kind: str, payload: Any) -> None:
        self._messages.put((kind, payload))

    def _poll_messages(self) -> None:
        if self.closed:
            return
        while True:
            try:
                kind, payload = self._messages.get_nowait()
            except queue.Empty:
                break
            if kind == "lifecycle":
                message = payload if isinstance(payload, dict) else {}
                if message.get("type") in {"shutdown", "eof"}:
                    self.close()
                    return
                if message.get("type") == "event":
                    self.root.lift()
            else:
                self.handle_message(kind, payload)
        self.root.after(50, self._poll_messages)

    def handle_message(self, _kind: str, _payload: Any) -> None:
        pass

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def close(self) -> None:
        if self.closed:
            return
        self._cancel_input_focus_check()
        self.closed = True
        with self._input_lock:
            desired = self._input_desired
            self._input_pending = None
            self._input_desired = (False, desired[1] if desired else "en")
        # A terminal close is an explicit hide request when this app asked the
        # role to be visible.  The provider also observes lifecycle closure,
        # but this ordered best-effort call avoids leaving the overlay up until
        # that later event and remains bounded when Core is already closing.
        if self._channel is not None and desired is not None and desired[0]:
            try:
                self._channel.call(
                    "role:input-method",
                    "hide",
                    {},
                    timeout=INPUT_METHOD_HIDE_TIMEOUT,
                )
            except MipcError:
                pass
        for unbind in self._wrap_bindings:
            try:
                unbind()
            except (RuntimeError, tk.TclError):
                pass
        if self._channel is not None:
            self._channel.close()
        self.root.destroy()


__all__ = [
    "ACCENT",
    "ACCENT_CONTAINER",
    "ACCENT_HOVER",
    "BG",
    "CARD_MAXIMUM_COLUMNS",
    "CARD_MINIMUM_WIDTH",
    "ERROR",
    "FIELD",
    "INPUT_METHOD_FOCUS_SETTLE_MS",
    "INPUT_METHOD_HIDE_TIMEOUT",
    "INPUT_METHOD_SHOW_TIMEOUT",
    "MUTED",
    "OUTLINE",
    "PANEL",
    "ResponsiveCardGrid",
    "SUCCESS",
    "SURFACE",
    "TEXT",
    "TouchApplication",
]
