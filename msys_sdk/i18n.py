"""Dependency-free reference implementation of ``msys.i18n.catalog.v1``.

The module implements named replacement and an optional plural-key convention.
It never calls ``eval`` or Python's unrestricted ``str.format`` machinery, so
the same JSON catalog and rendering behavior can be reproduced by non-Python
applications, including the small C runtime shipped beside this module.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Callable, Deque, Dict, Mapping, Optional, Sequence, Tuple


CATALOG_SCHEMA = "msys.i18n.catalog.v1"
CATALOG_META_SCHEMA = "https://msys.local/schemas/i18n-catalog.v1.json"
ENVIRONMENT_LOCALE_KEYS = ("MSYS_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG")
MIN_PLACEHOLDER_INTEGER = -9007199254740991
MAX_PLACEHOLDER_INTEGER = 9007199254740991
PLURAL_CATEGORIES = ("zero", "one", "two", "few", "many", "other")

_CATALOG_ID = re.compile(
    r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$"
)
_LOCALE = re.compile(
    r"^[a-z]{2,8}(?:-[A-Z][a-z]{3})?"
    r"(?:-(?:[A-Z]{2}|[0-9]{3}))?"
    r"(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*$"
)
_MESSAGE_KEY = re.compile(
    r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$"
)
_PLACEHOLDER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXTENSION_FIELD = re.compile(r"^x-[a-z0-9][a-z0-9._-]*$")
_ROOT_FIELDS = {
    "$schema",
    "schema",
    "id",
    "description",
    "default_locale",
    "messages",
}


class CatalogError(ValueError):
    """Raised when a catalog violates the structural or semantic contract."""

    def __init__(self, issues: Sequence[str]):
        self.issues = tuple(issues)
        super().__init__("invalid i18n catalog: " + "; ".join(self.issues))


@dataclass(frozen=True)
class I18nDiagnostic:
    """A recoverable translation problem suitable for logs or developer UI."""

    code: str
    catalog_id: str
    locale: str
    key: Optional[str]
    detail: str

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {
            "code": self.code,
            "catalog_id": self.catalog_id,
            "locale": self.locale,
            "key": self.key,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Catalog:
    """A validated, immutable i18n catalog."""

    id: str
    default_locale: str
    messages: Mapping[str, Mapping[str, str]]
    description: Optional[str] = None

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "Catalog":
        issues = _validate_catalog(document)
        if issues:
            raise CatalogError(issues)
        copied = {
            locale: MappingProxyType(dict(message_map))
            for locale, message_map in document["messages"].items()
        }
        return cls(
            id=document["id"],
            default_locale=document["default_locale"],
            messages=MappingProxyType(copied),
            description=document.get("description"),
        )

    @classmethod
    def load(cls, path: os.PathLike[str] | str) -> "Catalog":
        return cls.from_mapping(_load_json(Path(path)))


def load_catalog(path: os.PathLike[str] | str) -> Catalog:
    """Load and validate a UTF-8 ``msys.i18n.catalog.v1`` file."""

    return Catalog.load(path)


def normalize_locale(value: str) -> Optional[str]:
    """Normalize a common BCP-47/POSIX locale, or return ``None``.

    ``C``, ``C.UTF-8``, and ``POSIX`` also return ``None`` because their defined
    MSYS behavior is to use the catalog default rather than a locale map.
    """

    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    without_modifier = raw.split("@", 1)[0]
    without_encoding = without_modifier.split(".", 1)[0]
    if without_encoding.upper() in {"C", "POSIX"}:
        return None
    parts = without_encoding.replace("_", "-").split("-")
    if not parts or not (2 <= len(parts[0]) <= 8) or not parts[0].isalpha():
        return None

    canonical = [parts[0].lower()]
    index = 1
    if index < len(parts) and len(parts[index]) == 4 and parts[index].isalpha():
        canonical.append(parts[index].title())
        index += 1
    if index < len(parts) and (
        (len(parts[index]) == 2 and parts[index].isalpha())
        or (len(parts[index]) == 3 and parts[index].isdigit())
    ):
        canonical.append(parts[index].upper())
        index += 1
    for part in parts[index:]:
        if not part.isascii() or not part.isalnum():
            return None
        if not (5 <= len(part) <= 8 or (len(part) == 4 and part[0].isdigit())):
            return None
        canonical.append(part.lower())
    result = "-".join(canonical)
    return result if _LOCALE.fullmatch(result) is not None else None


def locale_from_environment(
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Return the first configured normalized locale, or ``None`` for default."""

    values = os.environ if environ is None else environ
    for name in ENVIRONMENT_LOCALE_KEYS:
        raw = values.get(name)
        if raw is None or not str(raw).strip():
            continue
        return normalize_locale(str(raw))
    return None


def locale_candidates(requested: str, default_locale: str) -> Tuple[str, ...]:
    """Build the normative parent-locale chain, ending in the default."""

    normalized = normalize_locale(requested)
    if normalized is None:
        normalized = default_locale
    candidates = []
    current = normalized
    while current:
        if current not in candidates:
            candidates.append(current)
        current = current.rpartition("-")[0]
    if default_locale not in candidates:
        candidates.append(default_locale)
    return tuple(candidates)


def plural_category(locale: str, count: int) -> str:
    """Return a compact CLDR-compatible cardinal category for an integer.

    MSYS deliberately avoids shipping ICU on small targets.  Catalogs express
    plural forms as ordinary v1 keys (``items.one``, ``items.other``), while
    this function supplies the common integer rules needed by system UIs.  It
    is deterministic, allocation-light, and shared with the C SDK.
    """

    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("plural count must be an integer")
    if count < MIN_PLACEHOLDER_INTEGER or count > MAX_PLACEHOLDER_INTEGER:
        raise ValueError(
            "plural count must be in %d..%d"
            % (MIN_PLACEHOLDER_INTEGER, MAX_PLACEHOLDER_INTEGER)
        )
    normalized = normalize_locale(locale) or "en"
    parts = normalized.split("-")
    language = parts[0]
    region = next(
        (part for part in parts[1:] if len(part) in (2, 3) and part.isupper()),
        "",
    )
    number = abs(count)
    mod10 = number % 10
    mod100 = number % 100

    if language in {"zh", "ja", "ko", "th", "vi", "id", "ms"}:
        return "other"
    if language == "ar":
        if number == 0:
            return "zero"
        if number == 1:
            return "one"
        if number == 2:
            return "two"
        if 3 <= mod100 <= 10:
            return "few"
        if 11 <= mod100 <= 99:
            return "many"
        return "other"
    if language in {"ru", "uk", "be"}:
        if mod10 == 1 and mod100 != 11:
            return "one"
        if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
            return "few"
        return "many"
    if language == "pl":
        if number == 1:
            return "one"
        if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
            return "few"
        return "many"
    if language in {"cs", "sk"}:
        if number == 1:
            return "one"
        if 2 <= number <= 4:
            return "few"
        return "other"
    if language == "sl":
        if mod100 == 1:
            return "one"
        if mod100 == 2:
            return "two"
        if mod100 in {3, 4}:
            return "few"
        return "other"
    if language == "lt":
        if mod10 == 1 and mod100 != 11:
            return "one"
        if 2 <= mod10 <= 9 and not 11 <= mod100 <= 19:
            return "few"
        return "other"
    if language == "lv":
        if mod10 == 0 or 11 <= mod100 <= 19:
            return "zero"
        if mod10 == 1 and mod100 != 11:
            return "one"
        return "other"
    if language == "ro":
        if number == 1:
            return "one"
        if number == 0 or 1 <= mod100 <= 19:
            return "few"
        return "other"
    if language == "he":
        if number == 1:
            return "one"
        if number == 2:
            return "two"
        if number != 0 and mod10 == 0:
            return "many"
        return "other"
    if language == "cy":
        return {0: "zero", 1: "one", 2: "two", 3: "few", 6: "many"}.get(
            number, "other"
        )
    if language == "ga":
        if number == 1:
            return "one"
        if number == 2:
            return "two"
        if 3 <= number <= 6:
            return "few"
        if 7 <= number <= 10:
            return "many"
        return "other"
    if language in {"fr", "hi"} or (language == "pt" and region != "PT"):
        return "one" if number in {0, 1} else "other"
    if language in {"is", "mk"}:
        return "one" if mod10 == 1 and mod100 != 11 else "other"
    return "one" if number == 1 else "other"


DiagnosticHandler = Callable[[I18nDiagnostic], None]


class Translator:
    """Resolve and safely render messages from one validated catalog."""

    def __init__(
        self,
        catalog: Catalog,
        locale: Optional[str] = None,
        *,
        environ: Optional[Mapping[str, str]] = None,
        on_diagnostic: Optional[DiagnosticHandler] = None,
        diagnostic_limit: int = 64,
    ) -> None:
        if not isinstance(catalog, Catalog):
            raise TypeError("catalog must be a validated Catalog")
        if (
            isinstance(diagnostic_limit, bool)
            or not isinstance(diagnostic_limit, int)
            or diagnostic_limit < 0
        ):
            raise ValueError("diagnostic_limit must be a non-negative integer")
        self.catalog = catalog
        self._on_diagnostic = on_diagnostic
        self._diagnostics: Deque[I18nDiagnostic] = deque(maxlen=diagnostic_limit)
        self._requested_locale = catalog.default_locale
        self._chain: Tuple[str, ...] = (catalog.default_locale,)
        self.set_locale(locale, environ=environ)

    @classmethod
    def from_file(
        cls,
        path: os.PathLike[str] | str,
        locale: Optional[str] = None,
        **kwargs: Any,
    ) -> "Translator":
        return cls(Catalog.load(path), locale, **kwargs)

    @property
    def locale(self) -> str:
        """The normalized requested locale (which may resolve to a fallback)."""

        return self._requested_locale

    @property
    def resolved_locale(self) -> str:
        """The first available catalog locale in the current chain."""

        return self._chain[0]

    @property
    def fallback_chain(self) -> Tuple[str, ...]:
        return self._chain

    @property
    def available_locales(self) -> Tuple[str, ...]:
        """Canonical locales present in the catalog, in resource order."""

        return tuple(self.catalog.messages)

    @property
    def diagnostics(self) -> Tuple[I18nDiagnostic, ...]:
        return tuple(self._diagnostics)

    def clear_diagnostics(self) -> None:
        self._diagnostics.clear()

    def set_locale(
        self,
        locale: Optional[str],
        *,
        environ: Optional[Mapping[str, str]] = None,
    ) -> str:
        """Select an explicit locale, or sample the environment when ``None``."""

        raw: Optional[str] = locale
        source = "explicit locale"
        if raw is None:
            source = "environment locale"
            values = os.environ if environ is None else environ
            for name in ENVIRONMENT_LOCALE_KEYS:
                candidate = values.get(name)
                if candidate is not None and str(candidate).strip():
                    raw = str(candidate)
                    source = name
                    break

        if raw is None or _selects_default(raw):
            requested = self.catalog.default_locale
        else:
            requested = normalize_locale(raw)
            if requested is None:
                requested = self.catalog.default_locale
                self._emit(
                    "invalid-locale",
                    None,
                    "%s value %r is invalid; using %s"
                    % (source, raw, self.catalog.default_locale),
                    locale=self.catalog.default_locale,
                )

        unfiltered = locale_candidates(requested, self.catalog.default_locale)
        available = tuple(item for item in unfiltered if item in self.catalog.messages)
        if not available:
            available = (self.catalog.default_locale,)
        self._requested_locale = requested
        self._chain = available
        if available[0] != requested:
            self._emit(
                "locale-fallback",
                None,
                "locale %s is unavailable; using %s" % (requested, available[0]),
                locale=requested,
            )
        return available[0]

    def text(
        self,
        key: str,
        params: Optional[Mapping[str, object]] = None,
        *,
        fallback: Optional[str] = None,
    ) -> str:
        """Translate ``key`` and apply named string/integer parameters safely.

        Missing keys return ``fallback`` or the key itself and emit a bounded
        diagnostic. Missing/invalid parameters remain visible as ``{name}``.
        """

        if not isinstance(key, str):
            raise TypeError("translation key must be a string")
        template, used_locale, _used_key = self._resolve((key,))
        if template is None:
            self._emit(
                "missing-key",
                key,
                "key is absent from locales %s" % ", ".join(self._chain),
            )
            if not isinstance(fallback, str):
                return key
            template = fallback

        if params is None:
            safe_params: Mapping[str, object] = {}
        elif isinstance(params, Mapping):
            safe_params = params
        else:
            self._emit(
                "invalid-parameters",
                key,
                "params must be a mapping; treating it as empty",
                locale=used_locale,
            )
            safe_params = {}
        return self._render(template, safe_params, key, used_locale)

    def plural(
        self,
        key: str,
        count: int,
        params: Optional[Mapping[str, object]] = None,
        *,
        fallback: Optional[str] = None,
    ) -> str:
        """Translate an integer cardinal using ordinary suffixed catalog keys.

        For ``key="tasks"``, the current locale is searched for
        ``tasks.<category>`` and then ``tasks.other`` before moving to its
        parent/default locale.  A legacy unsuffixed ``tasks`` key is the final
        compatibility fallback.  ``count`` is injected into interpolation
        parameters without mutating the caller's mapping.
        """

        if not isinstance(key, str):
            raise TypeError("translation key must be a string")
        template, used_locale, used_key = self._resolve_plural(key, count)
        if template is None:
            template, used_locale, used_key = self._resolve((key,))
        if template is None:
            self._emit(
                "missing-key",
                key,
                "plural forms for %s are absent from locales %s"
                % (key, ", ".join(self._chain)),
            )
            if not isinstance(fallback, str):
                return key
            template = fallback
            used_key = key

        if params is None:
            safe_params: Dict[str, object] = {}
        elif isinstance(params, Mapping):
            safe_params = dict(params)
        else:
            self._emit(
                "invalid-parameters",
                key,
                "params must be a mapping; treating it as empty",
                locale=used_locale,
            )
            safe_params = {}
        safe_params["count"] = count
        return self._render(template, safe_params, used_key or key, used_locale)

    __call__ = text

    def _resolve(
        self,
        keys: Sequence[str],
    ) -> Tuple[Optional[str], str, Optional[str]]:
        """Resolve candidate keys locale-first so plural overlays stay local."""

        for candidate in self._chain:
            message_map = self.catalog.messages[candidate]
            for key in keys:
                message = message_map.get(key)
                if message is not None:
                    return message, candidate, key
        return None, self._requested_locale, None

    def _resolve_plural(
        self,
        key: str,
        count: int,
    ) -> Tuple[Optional[str], str, Optional[str]]:
        """Resolve each locale with that locale's own grammar category."""

        for candidate in self._chain:
            category = plural_category(candidate, count)
            keys = tuple(
                dict.fromkeys(("%s.%s" % (key, category), "%s.other" % key))
            )
            message_map = self.catalog.messages[candidate]
            for plural_key in keys:
                message = message_map.get(plural_key)
                if message is not None:
                    return message, candidate, plural_key
        return None, self._requested_locale, None

    def _render(
        self,
        template: str,
        params: Mapping[str, object],
        key: str,
        locale: str,
    ) -> str:
        tokens, errors = _parse_template(template)
        if errors:
            self._emit(
                "invalid-template",
                key,
                "; ".join(errors),
                locale=locale,
            )
            return template

        output = []
        reported = set()
        for kind, value in tokens:
            if kind == "literal":
                output.append(value)
                continue
            argument = params.get(value, _MISSING)
            rendered: Optional[str]
            if isinstance(argument, str):
                rendered = argument
            elif (
                isinstance(argument, int)
                and not isinstance(argument, bool)
                and MIN_PLACEHOLDER_INTEGER <= argument <= MAX_PLACEHOLDER_INTEGER
            ):
                rendered = str(argument)
            else:
                rendered = None
            if rendered is None:
                output.append("{%s}" % value)
                if value not in reported:
                    code = "missing-placeholder" if argument is _MISSING else "invalid-placeholder-value"
                    detail = (
                        "placeholder %r has no argument" % value
                        if argument is _MISSING
                        else (
                            "placeholder %r requires a string or an integer in "
                            "%d..%d"
                            % (value, MIN_PLACEHOLDER_INTEGER, MAX_PLACEHOLDER_INTEGER)
                        )
                    )
                    self._emit(code, key, detail, locale=locale)
                    reported.add(value)
            else:
                output.append(rendered)
        return "".join(output)

    def _emit(
        self,
        code: str,
        key: Optional[str],
        detail: str,
        *,
        locale: Optional[str] = None,
    ) -> None:
        diagnostic = I18nDiagnostic(
            code=code,
            catalog_id=self.catalog.id,
            locale=locale or self._requested_locale,
            key=key,
            detail=detail,
        )
        self._diagnostics.append(diagnostic)
        if self._on_diagnostic is not None:
            try:
                self._on_diagnostic(diagnostic)
            except Exception:
                # Diagnostics must never turn a recoverable missing string into
                # an application crash. Applications can test callbacks alone.
                pass


_MISSING = object()


def _selects_default(value: str) -> bool:
    raw = str(value).strip().split("@", 1)[0].split(".", 1)[0]
    return not raw or raw.upper() in {"C", "POSIX"}


def _strict_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member %r" % key)
        result[key] = value
    return result


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("non-JSON numeric constant %s" % value)
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CatalogError(("%s: %s" % (path, exc),)) from exc
    if not isinstance(document, dict):
        raise CatalogError(("%s: catalog must be an object" % path,))
    return document


def _parse_template(template: str) -> Tuple[Tuple[Tuple[str, str], ...], Tuple[str, ...]]:
    tokens = []
    errors = []
    literal = []

    def flush_literal() -> None:
        if literal:
            tokens.append(("literal", "".join(literal)))
            literal.clear()

    index = 0
    while index < len(template):
        character = template[index]
        if character == "{":
            if index + 1 < len(template) and template[index + 1] == "{":
                literal.append("{")
                index += 2
                continue
            end = template.find("}", index + 1)
            if end < 0:
                errors.append("unclosed '{' at offset %d" % index)
                break
            name = template[index + 1 : end]
            if _PLACEHOLDER.fullmatch(name) is None:
                errors.append("invalid placeholder %r at offset %d" % (name, index))
            else:
                flush_literal()
                tokens.append(("placeholder", name))
            index = end + 1
            continue
        if character == "}":
            if index + 1 < len(template) and template[index + 1] == "}":
                literal.append("}")
                index += 2
                continue
            errors.append("unescaped '}' at offset %d" % index)
            index += 1
            continue
        literal.append(character)
        index += 1
    flush_literal()
    return tuple(tokens), tuple(errors)


def _validate_catalog(document: Mapping[str, Any]) -> Tuple[str, ...]:
    if not isinstance(document, Mapping):
        return ("catalog must be an object",)
    issues = []
    for field in document:
        if field not in _ROOT_FIELDS and (
            not isinstance(field, str) or _EXTENSION_FIELD.fullmatch(field) is None
        ):
            issues.append("unknown field %r" % field)
    for field in ("schema", "id", "default_locale", "messages"):
        if field not in document:
            issues.append("missing required field %r" % field)
    if document.get("schema") != CATALOG_SCHEMA:
        issues.append("schema must equal %r" % CATALOG_SCHEMA)
    if "$schema" in document and document["$schema"] != CATALOG_META_SCHEMA:
        issues.append("$schema must equal %r" % CATALOG_META_SCHEMA)
    catalog_id = document.get("id")
    if (
        not isinstance(catalog_id, str)
        or len(catalog_id) > 160
        or _CATALOG_ID.fullmatch(catalog_id) is None
    ):
        issues.append("invalid catalog id")
    description = document.get("description")
    if description is not None and (
        not isinstance(description, str)
        or not description
        or len(description) > 256
    ):
        issues.append("description must contain 1..256 characters")

    default_locale = document.get("default_locale")
    if (
        not isinstance(default_locale, str)
        or len(default_locale) > 63
        or _LOCALE.fullmatch(default_locale) is None
    ):
        issues.append("invalid canonical default_locale")
    messages = document.get("messages")
    if not isinstance(messages, Mapping) or not messages:
        issues.append("messages must be a non-empty object")
        return tuple(issues)
    if len(messages) > 128:
        issues.append("messages supports at most 128 locales")
    if isinstance(default_locale, str) and default_locale not in messages:
        issues.append("default_locale %r is absent from messages" % default_locale)

    placeholders: Dict[str, Dict[str, frozenset[str]]] = {}
    for locale, message_map in messages.items():
        if (
            not isinstance(locale, str)
            or len(locale) > 63
            or _LOCALE.fullmatch(locale) is None
        ):
            issues.append("invalid canonical locale %r" % locale)
        if not isinstance(message_map, Mapping) or not message_map:
            issues.append("locale %r must contain a non-empty message object" % locale)
            continue
        if len(message_map) > 20000:
            issues.append("locale %r contains more than 20000 messages" % locale)
        placeholders[locale] = {}
        for key, template in message_map.items():
            label = "%s.%s" % (locale, key)
            if (
                not isinstance(key, str)
                or len(key) > 160
                or _MESSAGE_KEY.fullmatch(key) is None
            ):
                issues.append("invalid message key %r in locale %r" % (key, locale))
            if not isinstance(template, str):
                issues.append("message %s must be a string" % label)
                continue
            if len(template) > 16384 or "\x00" in template:
                issues.append("message %s is too long or contains NUL" % label)
            tokens, template_errors = _parse_template(template)
            for error in template_errors:
                issues.append("message %s: %s" % (label, error))
            placeholders[locale][key] = frozenset(
                value for kind, value in tokens if kind == "placeholder"
            )

    default_messages = messages.get(default_locale)
    default_placeholders = placeholders.get(default_locale, {})
    if isinstance(default_messages, Mapping):
        for locale, message_map in messages.items():
            if locale == default_locale or not isinstance(message_map, Mapping):
                continue
            for key in message_map:
                if key not in default_messages:
                    issues.append(
                        "message %s.%s is absent from the default locale" % (locale, key)
                    )
                elif placeholders.get(locale, {}).get(key) != default_placeholders.get(key):
                    issues.append(
                        "message %s.%s placeholders do not match the default locale"
                        % (locale, key)
                    )
    return tuple(issues)


__all__ = [
    "CATALOG_META_SCHEMA",
    "CATALOG_SCHEMA",
    "Catalog",
    "CatalogError",
    "ENVIRONMENT_LOCALE_KEYS",
    "I18nDiagnostic",
    "MAX_PLACEHOLDER_INTEGER",
    "MIN_PLACEHOLDER_INTEGER",
    "PLURAL_CATEGORIES",
    "Translator",
    "load_catalog",
    "locale_candidates",
    "locale_from_environment",
    "normalize_locale",
    "plural_category",
]
