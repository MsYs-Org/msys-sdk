"""Small cross-toolkit font policy for MSYS graphical applications.

The module imports neither Tk nor Qt at module load time.  Applications keep
owning their widgets and rendering backend; this only normalizes the installed
family and fixed-panel pixel sizing used by the reference UI.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping


FONT_FAMILY_ENV = "MSYS_UI_FONT_FAMILY"
LEGACY_TK_FONT_FAMILY_ENV = "MSYS_TK_FONT_FAMILY"
MINIMUM_PIXEL_SIZE = 12
_ROOT_FAMILY_ATTRIBUTE = "_msys_tk_font_family"

PREFERRED_FAMILIES = (
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Noto Sans CJK",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "Noto Sans",
    "DejaVu Sans",
    "Liberation Sans",
    "Arial",
)

NAMED_TK_FONTS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkFixedFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)


def logical_size_to_pixels(size: int) -> int:
    """Convert a 96-DPI logical size, or normalize an explicit pixel size."""

    value = int(size)
    pixels = -value if value < 0 else (value * 4 + 1) // 3
    return max(MINIMUM_PIXEL_SIZE, pixels)


def requested_font_family(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the unified override, retaining the old Tk-only alias."""

    values = os.environ if environ is None else environ
    return str(
        values.get(FONT_FAMILY_ENV, "")
        or values.get(LEGACY_TK_FONT_FAMILY_ENV, "")
    ).strip()


def select_font_family(
    available: Iterable[object],
    requested: str = "",
) -> str | None:
    """Return the first installed family, preserving toolkit spelling."""

    installed = {
        str(family).strip().casefold(): str(family).strip()
        for family in available
        if str(family).strip()
    }
    candidates = ((requested.strip(),) if requested.strip() else ()) + PREFERRED_FAMILIES
    for candidate in candidates:
        match = installed.get(candidate.casefold())
        if match:
            return match
    return None


def configure_tk_fonts(
    root: Any,
    *,
    default_size: int | None = None,
) -> str | None:
    """Configure Tk's named fonts and supervised root-window identity."""

    from tkinter import TclError
    from tkinter import font as tkfont

    requested = requested_font_family()
    if requested:
        # The supervised profile names a font whose presence was already
        # verified by font-doctor.  Enumerating every Xft family here adds an
        # avoidable X11 round trip to the cold path of each Tk application;
        # on the small SPI target it can dominate input-method startup.
        family = requested
    else:
        try:
            available = tkfont.families(root=root)
        except (TclError, RuntimeError):
            available = ()
        family = select_font_family(available)
    if family is None:
        try:
            family = str(
                tkfont.nametofont("TkDefaultFont", root=root).actual("family")
            ).strip() or None
        except (TclError, RuntimeError):
            pass

    for name in NAMED_TK_FONTS:
        options: dict[str, object] = {}
        if family is not None:
            options["family"] = family
        if default_size is not None:
            options["size"] = -logical_size_to_pixels(default_size)
        try:
            tkfont.nametofont(name, root=root).configure(**options)
        except (TclError, RuntimeError):
            continue

    try:
        actual = str(
            tkfont.nametofont("TkDefaultFont", root=root).actual("family")
        ).strip()
        if actual:
            family = actual
    except (TclError, RuntimeError):
        pass
    if family is not None:
        setattr(root, _ROOT_FAMILY_ATTRIBUTE, family)

    # Every stock Tk application already enters through this shared setup
    # function.  Applying identity here fixes Settings/Apps without requiring
    # a framework-specific bootstrap or a resident helper.  Standalone tools
    # with no MSYS identity environment remain untouched.
    if any(
        os.environ.get(name)
        for name in (
            "MSYS_APP_ID",
            "MSYS_COMPONENT_ID",
            "MSYS_WINDOW_IDENTITY",
            "MSYS_WINDOW_ROLE",
        )
    ):
        from .ui_identity import configure_tk_window_identity

        configure_tk_window_identity(
            root,
            os.environ.get("MSYS_APP_ID", "org.msys.application"),
        )
    return family


def font_spec(widget: Any, size: int, *modifiers: str) -> tuple[object, ...]:
    """Build an explicit-size Tk font using the policy resolved for its root."""

    try:
        root = widget._root()
    except (AttributeError, RuntimeError):
        root = widget
    family = getattr(root, _ROOT_FAMILY_ATTRIBUTE, "sans-serif")
    return (family, -logical_size_to_pixels(size), *modifiers)


def _qt_style_strategy(qfont: Any, name: str) -> Any | None:
    owner = getattr(qfont, "StyleStrategy", None)
    if owner is not None:
        value = getattr(owner, name, None)
        if value is not None:
            return value
    return getattr(qfont, name, None)


def configure_qt_fonts(
    app: Any,
    qt_gui: Any,
    *,
    default_size: int = 10,
) -> str | None:
    """Apply the family, pixel size, and grayscale-AA policy to Qt."""

    try:
        available = qt_gui.QFontDatabase().families()
    except (AttributeError, RuntimeError, TypeError):
        available = ()
    family = select_font_family(available, requested_font_family())
    try:
        font = app.font()
        if family is not None:
            font.setFamily(family)
        font.setPixelSize(logical_size_to_pixels(default_size))
        no_subpixel = _qt_style_strategy(
            qt_gui.QFont,
            "NoSubpixelAntialias",
        )
        if no_subpixel is not None:
            # Passing the exact enum works with both Qt 5's flat constants and
            # Qt 6's nested Python enums; integer bitwise OR is not type-safe.
            font.setStyleStrategy(no_subpixel)
        app.setFont(font)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return family


__all__ = [
    "FONT_FAMILY_ENV",
    "LEGACY_TK_FONT_FAMILY_ENV",
    "MINIMUM_PIXEL_SIZE",
    "NAMED_TK_FONTS",
    "PREFERRED_FAMILIES",
    "configure_qt_fonts",
    "configure_tk_fonts",
    "font_spec",
    "logical_size_to_pixels",
    "requested_font_family",
    "select_font_family",
]
