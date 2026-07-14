from __future__ import annotations

from types import SimpleNamespace
import unittest

from msys_sdk.ui_layout import (
    bind_tk_text_wrap,
    configure_qt_scroll_area,
    configure_qt_text_wrap,
    content_width,
    responsive_columns,
)


class _TkWidget:
    def __init__(self, width: int = 240) -> None:
        self.width = width
        self.wraplength = None
        self.callback = None
        self.unbound = None

    def configure(self, **options) -> None:
        self.wraplength = options["wraplength"]

    def bind(self, event, callback, *, add):
        self.callback = callback
        self.binding = (event, add)
        return "binding-1"

    def unbind(self, event, binding_id) -> None:
        self.unbound = (event, binding_id)

    def winfo_width(self) -> int:
        return self.width


class _QtText:
    def __init__(self) -> None:
        self.wrapped = False
        self.minimum = -1

    def setWordWrap(self, value) -> None:
        self.wrapped = value

    def setMinimumWidth(self, value) -> None:
        self.minimum = value


class _Policy:
    ScrollBarAlwaysOff = object()
    ScrollBarAsNeeded = object()


class _QtCore:
    Qt = SimpleNamespace(ScrollBarPolicy=_Policy)


class _QtArea:
    def __init__(self) -> None:
        self.resizable = False
        self.content = None
        self.horizontal = None
        self.vertical = None

    def setWidgetResizable(self, value) -> None:
        self.resizable = value

    def setWidget(self, value) -> None:
        self.content = value

    def setHorizontalScrollBarPolicy(self, value) -> None:
        self.horizontal = value

    def setVerticalScrollBarPolicy(self, value) -> None:
        self.vertical = value


class ResponsiveLayoutTests(unittest.TestCase):
    def test_content_width_is_bounded_for_tiny_and_wide_rotations(self) -> None:
        self.assertEqual(content_width(20, horizontal_padding=32, minimum=96), 96)
        self.assertEqual(content_width(480, horizontal_padding=32), 448)
        self.assertEqual(content_width(1200, maximum=640), 640)

    def test_grid_columns_follow_available_width_without_zero(self) -> None:
        self.assertEqual(responsive_columns(20, minimum_item_width=120), 1)
        self.assertEqual(
            responsive_columns(500, minimum_item_width=120, gap=12),
            3,
        )
        self.assertEqual(
            responsive_columns(1200, minimum_item_width=120, maximum=4),
            4,
        )

    def test_tk_wrap_binding_updates_and_can_be_removed_independently(self) -> None:
        label = _TkWidget()
        container = _TkWidget(width=320)
        disconnect = bind_tk_text_wrap(
            label, container, horizontal_padding=40, minimum=80
        )
        self.assertEqual(label.wraplength, 280)
        self.assertEqual(container.binding, ("<Configure>", "+"))
        container.callback(SimpleNamespace(width=180))
        self.assertEqual(label.wraplength, 140)
        disconnect()
        self.assertEqual(container.unbound, ("<Configure>", "binding-1"))

    def test_qt_helpers_enable_wrap_and_vertical_scroll_policy(self) -> None:
        first = _QtText()
        second = _QtText()
        configure_qt_text_wrap(first, second)
        self.assertTrue(first.wrapped)
        self.assertEqual(second.minimum, 0)

        area = _QtArea()
        content = object()
        self.assertIs(
            configure_qt_scroll_area(area, content, qt_core=_QtCore), area
        )
        self.assertTrue(area.resizable)
        self.assertIs(area.content, content)
        self.assertIs(area.horizontal, _Policy.ScrollBarAlwaysOff)
        self.assertIs(area.vertical, _Policy.ScrollBarAsNeeded)


if __name__ == "__main__":
    unittest.main()
