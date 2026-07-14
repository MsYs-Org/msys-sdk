from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest import mock

from msys_sdk.ui_fonts import (
    MINIMUM_PIXEL_SIZE,
    NAMED_TK_FONTS,
    configure_qt_fonts,
    configure_tk_fonts,
    font_spec,
    logical_size_to_pixels,
    requested_font_family,
    select_font_family,
)


class _Root:
    def __init__(self, family: str = "before") -> None:
        self._msys_tk_font_family = family

    def _root(self):
        return self


class _Overlay:
    def __init__(self, root: _Root) -> None:
        self.root = root

    def _root(self):
        return self.root


class _NamedFont:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def configure(self, **options) -> None:
        self.options.update(options)

    def actual(self, option: str) -> str:
        if option != "family":
            raise AssertionError(option)
        return "fixed"


class _TkFonts:
    def __init__(self) -> None:
        self.named = {name: _NamedFont() for name in NAMED_TK_FONTS}

    def families(self, *, root) -> tuple[str, ...]:
        return ("Noto Sans CJK SC", "fixed")

    def nametofont(self, name: str, *, root) -> _NamedFont:
        return self.named[name]


class _Strategy:
    pass


NO_SUBPIXEL = _Strategy()


class _QtFont:
    class StyleStrategy:
        NoSubpixelAntialias = NO_SUBPIXEL


class _QtDatabase:
    families_result = ("Arial", "Noto Sans CJK SC")

    def families(self) -> tuple[str, ...]:
        return self.families_result


class _QtGui:
    QFont = _QtFont
    QFontDatabase = _QtDatabase


class _Font:
    def __init__(self) -> None:
        self.family = ""
        self.pixel_size = 0
        self.strategy = None

    def setFamily(self, family: str) -> None:
        self.family = family

    def setPixelSize(self, size: int) -> None:
        self.pixel_size = size

    def setStyleStrategy(self, strategy: object) -> None:
        if strategy is not NO_SUBPIXEL:
            raise TypeError("Qt binding requires its enum value")
        self.strategy = strategy


class _App:
    def __init__(self) -> None:
        self.value = _Font()
        self.installed = None

    def font(self) -> _Font:
        return self.value

    def setFont(self, font: _Font) -> None:
        self.installed = font


class UiFontPolicyTests(unittest.TestCase):
    def test_pixel_conversion_has_one_minimum_for_logical_and_pixel_input(self) -> None:
        self.assertEqual(logical_size_to_pixels(8), MINIMUM_PIXEL_SIZE)
        self.assertEqual(logical_size_to_pixels(10), 13)
        self.assertEqual(logical_size_to_pixels(14), 19)
        self.assertEqual(logical_size_to_pixels(-8), MINIMUM_PIXEL_SIZE)
        self.assertEqual(logical_size_to_pixels(-16), 16)

    def test_family_override_and_selection_are_shared(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MSYS_UI_FONT_FAMILY": "Custom", "MSYS_TK_FONT_FAMILY": "Legacy"},
            clear=True,
        ):
            self.assertEqual(requested_font_family(), "Custom")
        self.assertEqual(
            select_font_family(("Arial", "Noto Sans CJK SC")),
            "Noto Sans CJK SC",
        )
        self.assertEqual(select_font_family(("Custom",), "custom"), "Custom")

    def test_tk_configures_every_named_font_in_pixels_and_reports_reality(self) -> None:
        api = _TkFonts()
        tkinter = SimpleNamespace(TclError=RuntimeError, font=api)
        root = _Root()
        with (
            mock.patch.dict("sys.modules", {"tkinter": tkinter}),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(configure_tk_fonts(root, default_size=10), "fixed")
        self.assertEqual(root._msys_tk_font_family, "fixed")
        self.assertTrue(
            all(font.options == {"family": "Noto Sans CJK SC", "size": -13}
                for font in api.named.values())
        )
        self.assertEqual(font_spec(_Overlay(root), 11), ("fixed", -15))

    def test_tk_supervisor_environment_also_applies_canonical_identity(self) -> None:
        api = _TkFonts()
        tkinter = SimpleNamespace(TclError=RuntimeError, font=api)
        root = _Root()
        with (
            mock.patch.dict("sys.modules", {"tkinter": tkinter}),
            mock.patch.dict(
                os.environ,
                {
                    "MSYS_APP_ID": "org.msys.settings",
                    "MSYS_COMPONENT_ID": "org.msys.settings:main",
                },
                clear=True,
            ),
            mock.patch(
                "msys_sdk.ui_identity.configure_tk_window_identity"
            ) as configure_identity,
        ):
            configure_tk_fonts(root)
        configure_identity.assert_called_once_with(root, "org.msys.settings")

    def test_qt_uses_typed_strategy_and_applies_policy_without_a_family(self) -> None:
        app = _App()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(configure_qt_fonts(app, _QtGui), "Noto Sans CJK SC")
        self.assertIs(app.installed, app.value)
        self.assertEqual(app.value.pixel_size, 13)
        self.assertIs(app.value.strategy, NO_SUBPIXEL)

        _QtDatabase.families_result = ()
        try:
            fallback = _App()
            self.assertIsNone(configure_qt_fonts(fallback, _QtGui))
            self.assertIs(fallback.installed, fallback.value)
            self.assertEqual(fallback.value.pixel_size, 13)
            self.assertIs(fallback.value.strategy, NO_SUBPIXEL)
        finally:
            _QtDatabase.families_result = ("Arial", "Noto Sans CJK SC")


if __name__ == "__main__":
    unittest.main()
