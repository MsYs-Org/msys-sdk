from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from msys_sdk import (
    Catalog,
    CatalogError,
    Translator,
    load_catalog,
    locale_candidates,
    locale_from_environment,
    normalize_locale,
    plural_category,
)


def catalog_document() -> dict:
    return {
        "$schema": "https://msys.local/schemas/i18n-catalog.v1.json",
        "schema": "msys.i18n.catalog.v1",
        "id": "org.example.test",
        "description": "Test strings",
        "default_locale": "en-US",
        "messages": {
            "en-US": {
                "app.title": "Example",
                "greeting": "Hello, {name}",
                "tasks.count": "Tasks: {count}",
                "tasks.one": "One task",
                "tasks.other": "{count} tasks",
                "literal.braces": "{{value}} = {value}",
            },
            "zh": {
                "app.title": "示例",
                "greeting": "你好，{name}",
                "tasks.other": "{count} 项任务",
            },
            "zh-CN": {
                "greeting": "您好，{name}",
            },
        },
    }


class LocaleTests(unittest.TestCase):
    def test_normalizes_posix_and_bcp47_spelling(self) -> None:
        cases = {
            "zh_CN.UTF-8": "zh-CN",
            "zh-hans-cn": "zh-Hans-CN",
            "EN_us@calendar": "en-US",
            "es_419": "es-419",
            "sl-rozaj": "sl-rozaj",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_locale(raw), expected)

    def test_c_posix_and_invalid_values_select_default(self) -> None:
        for raw in ("C", "C.UTF-8", "POSIX", "", "not_a_valid_locale"):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_locale(raw))

    def test_environment_precedence_is_stable(self) -> None:
        environment = {
            "LANG": "en_US.UTF-8",
            "LC_MESSAGES": "zh_CN.UTF-8",
            "MSYS_LOCALE": "zh-Hans-CN",
        }
        self.assertEqual(locale_from_environment(environment), "zh-Hans-CN")
        del environment["MSYS_LOCALE"]
        self.assertEqual(locale_from_environment(environment), "zh-CN")

    def test_candidate_chain_uses_parents_then_default(self) -> None:
        self.assertEqual(
            locale_candidates("zh-Hans-CN", "en-US"),
            ("zh-Hans-CN", "zh-Hans", "zh", "en-US"),
        )

    def test_integer_plural_rules_cover_common_small_device_locales(self) -> None:
        cases = {
            ("zh-CN", 1): "other",
            ("en-US", 1): "one",
            ("en-US", 2): "other",
            ("fr-FR", 0): "one",
            ("ru-RU", 1): "one",
            ("ru-RU", 3): "few",
            ("ru-RU", 12): "many",
            ("ar", 0): "zero",
            ("ar", 2): "two",
            ("ar", 7): "few",
        }
        for (locale, count), expected in cases.items():
            with self.subTest(locale=locale, count=count):
                self.assertEqual(plural_category(locale, count), expected)

        with self.assertRaises(TypeError):
            plural_category("en-US", True)


class CatalogTests(unittest.TestCase):
    def test_loads_strict_utf8_json_and_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(json.dumps(catalog_document(), ensure_ascii=False), encoding="utf-8")
            catalog = load_catalog(path)
        self.assertEqual(catalog.id, "org.example.test")
        with self.assertRaises(TypeError):
            catalog.messages["en-US"]["app.title"] = "Changed"  # type: ignore[index]

    def test_rejects_duplicate_members_and_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            duplicate = Path(temporary) / "duplicate.json"
            duplicate.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            with self.assertRaises(CatalogError):
                Catalog.load(duplicate)
            bom = Path(temporary) / "bom.json"
            bom.write_text("\ufeff" + json.dumps(catalog_document()), encoding="utf-8")
            with self.assertRaises(CatalogError):
                Catalog.load(bom)

    def test_rejects_unknown_keys_and_placeholder_drift(self) -> None:
        unknown = catalog_document()
        unknown["messages"]["zh-CN"]["only.translation"] = "额外"
        with self.assertRaisesRegex(CatalogError, "absent from the default"):
            Catalog.from_mapping(unknown)

        drift = catalog_document()
        drift["messages"]["zh-CN"]["greeting"] = "您好，{person}"
        with self.assertRaisesRegex(CatalogError, "placeholders"):
            Catalog.from_mapping(drift)

    def test_rejects_unrestricted_format_syntax(self) -> None:
        for template in ("{user.name}", "{name!r}", "{count:04d}", "broken {"):
            document = catalog_document()
            document["messages"]["en-US"]["greeting"] = template
            with self.subTest(template=template), self.assertRaises(CatalogError):
                Catalog.from_mapping(document)


class TranslatorTests(unittest.TestCase):
    def make_translator(self, locale: str | None = "zh-CN", **kwargs) -> Translator:
        return Translator(Catalog.from_mapping(catalog_document()), locale, **kwargs)

    def test_explicit_locale_overrides_environment(self) -> None:
        translator = self.make_translator(
            "en-US", environ={"MSYS_LOCALE": "zh_CN.UTF-8"}
        )
        self.assertEqual(translator.text("app.title"), "Example")

    def test_environment_locale_and_parent_fallback(self) -> None:
        translator = self.make_translator(
            None, environ={"MSYS_LOCALE": "zh_CN.UTF-8"}
        )
        self.assertEqual(translator.locale, "zh-CN")
        self.assertEqual(translator.fallback_chain, ("zh-CN", "zh", "en-US"))
        self.assertEqual(translator.text("greeting", {"name": "小明"}), "您好，小明")
        self.assertEqual(translator.text("app.title"), "示例")
        self.assertEqual(translator.text("tasks.count", {"count": 3}), "Tasks: 3")

    def test_unavailable_locale_reports_and_uses_default(self) -> None:
        translator = self.make_translator("fr-FR")
        self.assertEqual(translator.resolved_locale, "en-US")
        self.assertEqual(translator.text("app.title"), "Example")
        self.assertEqual(translator.diagnostics[0].code, "locale-fallback")

    def test_locale_can_be_switched_without_global_mutation(self) -> None:
        translator = self.make_translator("en-US")
        self.assertEqual(translator.text("app.title"), "Example")
        self.assertEqual(translator.set_locale("zh_CN.UTF-8"), "zh-CN")
        self.assertEqual(translator.text("app.title"), "示例")

    def test_missing_key_is_visible_diagnostic_and_never_raises(self) -> None:
        translator = self.make_translator()
        self.assertEqual(translator.text("missing.label"), "missing.label")
        self.assertEqual(
            translator.text("missing.welcome", {"name": "Ada"}, fallback="Hi, {name}"),
            "Hi, Ada",
        )
        missing = [item for item in translator.diagnostics if item.code == "missing-key"]
        self.assertEqual([item.key for item in missing], ["missing.label", "missing.welcome"])
        self.assertEqual(missing[0].as_dict()["catalog_id"], "org.example.test")

    def test_named_rendering_escapes_braces_and_never_reparses_values(self) -> None:
        translator = self.make_translator("en-US")
        self.assertEqual(
            translator.text("literal.braces", {"value": "{not_a_placeholder}"}),
            "{value} = {not_a_placeholder}",
        )

    def test_plural_is_locale_first_and_injects_count_without_mutation(self) -> None:
        translator = self.make_translator("zh-CN")
        caller_params = {"count": 999}
        self.assertEqual(translator.plural("tasks", 1, caller_params), "1 项任务")
        self.assertEqual(caller_params, {"count": 999})

        translator.set_locale("en-US")
        self.assertEqual(translator.plural("tasks", 1), "One task")
        self.assertEqual(translator.plural("tasks", 4), "4 tasks")

    def test_plural_falls_back_to_legacy_key_or_visible_safe_value(self) -> None:
        translator = self.make_translator("en-US")
        self.assertEqual(translator.plural("tasks.count", 2), "Tasks: 2")
        self.assertEqual(translator.plural("missing.items", 2), "missing.items")
        self.assertEqual(
            translator.plural("missing.rows", 2, fallback="{count} rows"),
            "2 rows",
        )

    def test_plural_uses_the_fallback_message_locales_grammar(self) -> None:
        document = catalog_document()
        document["messages"] = {"en-US": document["messages"]["en-US"]}
        translator = Translator(Catalog.from_mapping(document), "zh-CN")
        self.assertEqual(translator.plural("tasks", 1), "One task")

    def test_missing_and_invalid_placeholder_values_remain_visible(self) -> None:
        translator = self.make_translator("en-US")
        self.assertEqual(translator.text("greeting"), "Hello, {name}")
        self.assertEqual(translator.text("tasks.count", {"count": True}), "Tasks: {count}")
        self.assertEqual(
            translator.text("tasks.count", {"count": 9007199254740992}),
            "Tasks: {count}",
        )
        self.assertEqual(
            [item.code for item in translator.diagnostics],
            [
                "missing-placeholder",
                "invalid-placeholder-value",
                "invalid-placeholder-value",
            ],
        )

    def test_bad_diagnostic_callback_cannot_crash_translation(self) -> None:
        def broken_callback(_diagnostic) -> None:
            raise RuntimeError("log sink failed")

        translator = self.make_translator(on_diagnostic=broken_callback)
        self.assertEqual(translator.text("missing.label"), "missing.label")

    def test_diagnostics_are_bounded_and_can_be_disabled(self) -> None:
        translator = self.make_translator(diagnostic_limit=2)
        for number in range(4):
            translator.text("missing.%d" % number)
        self.assertEqual(len(translator.diagnostics), 2)
        translator.clear_diagnostics()
        self.assertEqual(translator.diagnostics, ())

        disabled = self.make_translator(diagnostic_limit=0)
        disabled.text("missing.label")
        self.assertEqual(disabled.diagnostics, ())

    def test_invalid_locale_is_diagnostic_not_exception(self) -> None:
        translator = self.make_translator("invalid_locale_x")
        self.assertEqual(translator.resolved_locale, "en-US")
        self.assertEqual(translator.diagnostics[0].code, "invalid-locale")


if __name__ == "__main__":
    unittest.main()
