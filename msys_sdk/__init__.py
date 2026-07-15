"""Public MSYS SDK API with a small IPC-first import path.

Most supervised helpers only need :class:`MsysClient` during their readiness
handshake.  Importing Tk, manifest tooling, i18n generators and every UI helper
before that edge made small on-demand providers needlessly slow and large.
The public names stay unchanged; non-client modules are loaded on first use.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .client import (
    MsysClient,
    MsysClientError,
    MsysConnectionClosed,
    MsysMessage,
    MsysProtocolError,
    MsysShutdown,
)


__version__ = "0.1.13"

_LAZY_EXPORTS = {
    "AppManifestError": "app_manifest",
    "SUPPORTED_RUNTIMES": "app_manifest",
    "create_application_manifest": "app_manifest",
    "write_manifest": "app_manifest",
    "Catalog": "i18n",
    "CatalogError": "i18n",
    "I18nDiagnostic": "i18n",
    "PLURAL_CATEGORIES": "i18n",
    "Translator": "i18n",
    "load_catalog": "i18n",
    "locale_candidates": "i18n",
    "locale_from_environment": "i18n",
    "normalize_locale": "i18n",
    "plural_category": "i18n",
    "render_c_header": "i18n_c",
    "write_c_header": "i18n_c",
    "configure_qt_fonts": "ui_fonts",
    "configure_tk_fonts": "ui_fonts",
    "font_spec": "ui_fonts",
    "logical_size_to_pixels": "ui_fonts",
    "requested_font_family": "ui_fonts",
    "select_font_family": "ui_fonts",
    "TkScrollablePage": "ui_layout",
    "bind_tk_text_wrap": "ui_layout",
    "configure_qt_scroll_area": "ui_layout",
    "configure_qt_text_wrap": "ui_layout",
    "content_width": "ui_layout",
    "responsive_columns": "ui_layout",
    "WindowIdentity": "ui_identity",
    "configure_tk_window_identity": "ui_identity",
    "window_identity": "ui_identity",
    "TkInputMethodBinding": "ui_input_method",
    "bind_tk_input_method": "ui_input_method",
    "ComponentChannel": "component_ipc",
    "ComponentCallback": "component_ipc",
    "ComponentCallHandler": "component_ipc",
    "MipcError": "component_ipc",
    "MipcRemoteError": "component_ipc",
    "MipcUnavailable": "component_ipc",
    "PublicMipcClient": "component_ipc",
    "APPLICATION_NAVIGATION_INTERFACE": "application_navigation",
    "NAVIGATION_BACK_METHOD": "application_navigation",
    "NavigationBackCallback": "application_navigation",
    "application_navigation_handler": "application_navigation",
    "ResponsiveCardGrid": "tk_app",
    "TouchApplication": "tk_app",
    "PackageI18n": "package_i18n",
}

__all__ = [
    "MsysClient",
    "MsysClientError",
    "MsysConnectionClosed",
    "MsysMessage",
    "MsysProtocolError",
    "MsysShutdown",
    *_LAZY_EXPORTS,
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
