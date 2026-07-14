#ifndef MSYS_I18N_H
#define MSYS_I18N_H

/*
 * Tiny, allocation-free i18n lookup/rendering API.
 *
 * A validated msys.i18n.catalog.v1 JSON resource is converted at build time
 * to an msys_i18n_entry array.  The Python SDK's ``msys-i18n-c`` command can
 * generate that header; applications therefore need no JSON parser or ICU at
 * runtime and C, C++, Python, Qt, and Tk continue to share one source file.
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MSYS_I18N_LOCALE_CAPACITY 64u

enum msys_i18n_result {
    MSYS_I18N_OK = 0,
    MSYS_I18N_NOT_FOUND = 1,
    MSYS_I18N_BUFFER_TOO_SMALL = 2,
    MSYS_I18N_INVALID_ARGUMENT = -1,
    MSYS_I18N_INVALID_LOCALE = -2,
    MSYS_I18N_INVALID_TEMPLATE = -3
};

typedef struct msys_i18n_entry {
    const char *locale;
    const char *key;
    const char *value;
} msys_i18n_entry;

typedef struct msys_i18n_catalog {
    const char *id;
    const char *default_locale;
    const msys_i18n_entry *entries;
    size_t entry_count;
} msys_i18n_catalog;

typedef struct msys_i18n_param {
    const char *name;
    const char *value;
} msys_i18n_param;

/* POSIX spellings such as zh_CN.UTF-8 normalize to canonical zh-CN. */
int msys_i18n_normalize_locale(
    const char *input,
    char *output,
    size_t capacity
);

/* Sample MSYS_LOCALE, LC_ALL, LC_MESSAGES, then LANG. */
int msys_i18n_locale_from_environment(char *output, size_t capacity);

/* Locale-parent lookup ending at catalog->default_locale; NULL means missing. */
const char *msys_i18n_lookup(
    const msys_i18n_catalog *catalog,
    const char *locale,
    const char *key
);

/* Returns one of zero, one, two, few, many, or other. */
const char *msys_i18n_plural_category(const char *locale, int64_t count);

/* Looks up key.<category>, then key.other, then the legacy unsuffixed key. */
const char *msys_i18n_lookup_plural(
    const msys_i18n_catalog *catalog,
    const char *locale,
    const char *key,
    int64_t count
);

/*
 * Single-pass {name} replacement with {{ and }} escapes.  Missing parameters
 * stay visible. required receives the byte count excluding NUL even when the
 * output buffer is too small, which makes a retry straightforward.
 */
int msys_i18n_render(
    const char *message,
    const msys_i18n_param *params,
    size_t param_count,
    char *output,
    size_t capacity,
    size_t *required
);

#ifdef __cplusplus
}
#endif

#endif
