"""Small package-local facade for the shared MSYS i18n contract."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .i18n import CatalogError, Translator


class PackageI18n:
    """Translate one package catalog with explicit recovery strings.

    Applications own their catalogs and fallback text; the SDK only owns the
    language selection and safe formatting behavior.
    """

    def __init__(
        self,
        catalog_path: str | os.PathLike[str],
        fallback: Mapping[str, str],
        *,
        locale: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        configured = (environ or os.environ).get("MSYS_I18N_CATALOG")
        self.catalog_path = Path(configured or catalog_path)
        self.fallback = dict(fallback)
        self.load_error = ""
        self._translator: Translator | None = None
        try:
            self._translator = Translator.from_file(
                self.catalog_path,
                locale,
                environ=environ,
            )
        except (CatalogError, OSError, UnicodeError, ValueError) as exc:
            self.load_error = str(exc)

    @property
    def locale(self) -> str:
        if self._translator is None:
            return "en-US"
        return str(self._translator.resolved_locale)

    @property
    def fallback_chain(self) -> tuple[str, ...]:
        if self._translator is None:
            return ("en-US",)
        return tuple(str(item) for item in self._translator.fallback_chain)

    def set_locale(
        self,
        locale: str | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> str:
        if self._translator is None:
            return "en-US"
        return str(self._translator.set_locale(locale, environ=environ))

    def text(
        self,
        key: str,
        params: Mapping[str, object] | None = None,
        *,
        fallback: str | None = None,
    ) -> str:
        english = fallback if fallback is not None else self.fallback.get(key, key)
        if self._translator is not None:
            return str(self._translator.text(key, params, fallback=english))
        rendered = english
        for name, value in (params or {}).items():
            if isinstance(value, str) or (
                isinstance(value, int) and not isinstance(value, bool)
            ):
                rendered = rendered.replace("{" + str(name) + "}", str(value))
        return rendered

    __call__ = text


__all__ = ["PackageI18n"]
