#include "msys/i18n.h"

#include <stdio.h>
#include <string.h>

#define CHECK(expression) do { \
    if (!(expression)) { \
        fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #expression); \
        return 1; \
    } \
} while (0)

static const msys_i18n_entry entries[] = {
    {"en-US", "greeting", "Hello, {name}"},
    {"en-US", "tasks.one", "One task"},
    {"en-US", "tasks.other", "{count} tasks"},
    {"zh", "greeting", "你好，{name}"},
    {"zh", "tasks.other", "{count} 项任务"},
    {"zh-CN", "greeting", "您好，{name}"}
};

static const msys_i18n_catalog catalog = {
    "org.example.test",
    "en-US",
    entries,
    sizeof(entries) / sizeof(entries[0])
};

static const msys_i18n_entry zh_entries[] = {
    {"zh-CN", "items.one", "wrong"},
    {"zh-CN", "items.other", "correct"}
};

static const msys_i18n_catalog zh_catalog = {
    "org.example.zh",
    "zh-CN",
    zh_entries,
    sizeof(zh_entries) / sizeof(zh_entries[0])
};

static const msys_i18n_entry en_only_entries[] = {
    {"en-US", "items.one", "one"},
    {"en-US", "items.other", "other"}
};

static const msys_i18n_catalog en_only_catalog = {
    "org.example.en",
    "en-US",
    en_only_entries,
    sizeof(en_only_entries) / sizeof(en_only_entries[0])
};

int main(void)
{
    char locale[MSYS_I18N_LOCALE_CAPACITY];
    char output[64];
    char small[4];
    size_t required = 0u;
    msys_i18n_param params[] = {{"name", "Ada"}, {"count", "3"}};

    CHECK(msys_i18n_normalize_locale("zh_cn.UTF-8", locale, sizeof(locale)) == MSYS_I18N_OK);
    CHECK(strcmp(locale, "zh-CN") == 0);
    CHECK(msys_i18n_normalize_locale("C.UTF-8", locale, sizeof(locale)) == MSYS_I18N_INVALID_LOCALE);

    CHECK(strcmp(msys_i18n_lookup(&catalog, "zh-Hans-CN", "greeting"), "你好，{name}") == 0);
    CHECK(strcmp(msys_i18n_lookup(&catalog, "de-DE", "greeting"), "Hello, {name}") == 0);
    CHECK(msys_i18n_lookup(&catalog, "zh-CN", "missing") == NULL);

    CHECK(strcmp(msys_i18n_plural_category("zh-CN", 1), "other") == 0);
    CHECK(strcmp(msys_i18n_plural_category("ru-RU", 3), "few") == 0);
    CHECK(strcmp(msys_i18n_lookup_plural(&catalog, "zh-CN", "tasks", 1), "{count} 项任务") == 0);
    CHECK(strcmp(msys_i18n_lookup_plural(&catalog, "en-US", "tasks", 1), "One task") == 0);
    CHECK(strcmp(msys_i18n_lookup_plural(&zh_catalog, NULL, "items", 1), "correct") == 0);
    CHECK(strcmp(msys_i18n_lookup_plural(&en_only_catalog, "zh-CN", "items", 1), "one") == 0);

    CHECK(msys_i18n_render("Hello, {name}", params, 2u, output, sizeof(output), &required) == MSYS_I18N_OK);
    CHECK(strcmp(output, "Hello, Ada") == 0);
    CHECK(required == strlen("Hello, Ada"));
    CHECK(msys_i18n_render("{{{missing}}}", params, 2u, output, sizeof(output), NULL) == MSYS_I18N_OK);
    CHECK(strcmp(output, "{{missing}}") == 0);
    CHECK(msys_i18n_render("{count} tasks", params, 2u, small, sizeof(small), &required) == MSYS_I18N_BUFFER_TOO_SMALL);
    CHECK(required == strlen("3 tasks"));
    CHECK(strcmp(small, "3 t") == 0);
    CHECK(msys_i18n_render("bad {name.x}", params, 2u, output, sizeof(output), NULL) == MSYS_I18N_INVALID_TEMPLATE);

    puts("i18n C SDK tests passed");
    return 0;
}
