#include "msys/i18n.h"

#include <ctype.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int ascii_equal_fold(const char *left, const char *right)
{
    while (*left != '\0' && *right != '\0') {
        if (tolower((unsigned char)*left) != tolower((unsigned char)*right)) {
            return 0;
        }
        ++left;
        ++right;
    }
    return *left == '\0' && *right == '\0';
}

int msys_i18n_normalize_locale(
    const char *input,
    char *output,
    size_t capacity
)
{
    char raw[MSYS_I18N_LOCALE_CAPACITY];
    size_t length = 0u;
    size_t index;
    size_t part = 0u;
    size_t part_start = 0u;
    int saw_script = 0;

    if (input == NULL || output == NULL || capacity == 0u) {
        return MSYS_I18N_INVALID_ARGUMENT;
    }
    while (isspace((unsigned char)*input) != 0) {
        ++input;
    }
    while (*input != '\0' && *input != '.' && *input != '@' &&
           isspace((unsigned char)*input) == 0) {
        unsigned char byte = (unsigned char)*input++;
        if (length + 1u >= sizeof(raw)) {
            return MSYS_I18N_INVALID_LOCALE;
        }
        raw[length++] = byte == '_' ? '-' : (char)byte;
    }
    raw[length] = '\0';
    if (length == 0u || ascii_equal_fold(raw, "C") || ascii_equal_fold(raw, "POSIX")) {
        return MSYS_I18N_INVALID_LOCALE;
    }
    if (length + 1u > capacity) {
        return MSYS_I18N_BUFFER_TOO_SMALL;
    }

    for (index = 0u; index <= length; ++index) {
        if (index != length && raw[index] != '-') {
            continue;
        }
        if (index == part_start) {
            return MSYS_I18N_INVALID_LOCALE;
        }
        if (part == 0u) {
            size_t cursor;
            if (index - part_start < 2u || index - part_start > 8u) {
                return MSYS_I18N_INVALID_LOCALE;
            }
            for (cursor = part_start; cursor < index; ++cursor) {
                if (isalpha((unsigned char)raw[cursor]) == 0) {
                    return MSYS_I18N_INVALID_LOCALE;
                }
                raw[cursor] = (char)tolower((unsigned char)raw[cursor]);
            }
        } else if (part == 1u && index - part_start == 4u) {
            size_t cursor;
            for (cursor = part_start; cursor < index; ++cursor) {
                if (isalpha((unsigned char)raw[cursor]) == 0) {
                    break;
                }
            }
            if (cursor == index) {
                saw_script = 1;
                raw[part_start] = (char)toupper((unsigned char)raw[part_start]);
                for (cursor = part_start + 1u; cursor < index; ++cursor) {
                    raw[cursor] = (char)tolower((unsigned char)raw[cursor]);
                }
            } else {
                if (isdigit((unsigned char)raw[part_start]) == 0) {
                    return MSYS_I18N_INVALID_LOCALE;
                }
                for (cursor = part_start; cursor < index; ++cursor) {
                    if (isalnum((unsigned char)raw[cursor]) == 0) {
                        return MSYS_I18N_INVALID_LOCALE;
                    }
                    raw[cursor] = (char)tolower((unsigned char)raw[cursor]);
                }
            }
        } else if (part == (size_t)(1 + saw_script) && index - part_start == 2u) {
            size_t cursor;
            for (cursor = part_start; cursor < index; ++cursor) {
                if (isalpha((unsigned char)raw[cursor]) == 0) {
                    return MSYS_I18N_INVALID_LOCALE;
                }
                raw[cursor] = (char)toupper((unsigned char)raw[cursor]);
            }
        } else if (part == (size_t)(1 + saw_script) && index - part_start == 3u) {
            size_t cursor;
            for (cursor = part_start; cursor < index; ++cursor) {
                if (isdigit((unsigned char)raw[cursor]) == 0) {
                    return MSYS_I18N_INVALID_LOCALE;
                }
            }
        } else {
            size_t cursor;
            size_t size = index - part_start;
            if (!((size >= 5u && size <= 8u) ||
                  (size == 4u && isdigit((unsigned char)raw[part_start]) != 0))) {
                return MSYS_I18N_INVALID_LOCALE;
            }
            for (cursor = part_start; cursor < index; ++cursor) {
                if (isalnum((unsigned char)raw[cursor]) == 0) {
                    return MSYS_I18N_INVALID_LOCALE;
                }
                raw[cursor] = (char)tolower((unsigned char)raw[cursor]);
            }
        }
        ++part;
        part_start = index + 1u;
    }
    memcpy(output, raw, length + 1u);
    return MSYS_I18N_OK;
}

int msys_i18n_locale_from_environment(char *output, size_t capacity)
{
    static const char *const names[] = {
        "MSYS_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG"
    };
    size_t index;
    if (output == NULL || capacity == 0u) {
        return MSYS_I18N_INVALID_ARGUMENT;
    }
    for (index = 0u; index < sizeof(names) / sizeof(names[0]); ++index) {
        const char *value = getenv(names[index]);
        if (value != NULL && *value != '\0') {
            return msys_i18n_normalize_locale(value, output, capacity);
        }
    }
    return MSYS_I18N_NOT_FOUND;
}

static const char *lookup_exact(
    const msys_i18n_catalog *catalog,
    const char *locale,
    const char *key
)
{
    size_t index;
    for (index = 0u; index < catalog->entry_count; ++index) {
        const msys_i18n_entry *entry = &catalog->entries[index];
        if (strcmp(entry->locale, locale) == 0 && strcmp(entry->key, key) == 0) {
            return entry->value;
        }
    }
    return NULL;
}

static int requested_locale(
    const msys_i18n_catalog *catalog,
    const char *locale,
    char output[MSYS_I18N_LOCALE_CAPACITY]
)
{
    const char *selected = locale;
    if (selected == NULL || *selected == '\0' ||
        msys_i18n_normalize_locale(selected, output, MSYS_I18N_LOCALE_CAPACITY)
            != MSYS_I18N_OK) {
        selected = catalog->default_locale;
        return msys_i18n_normalize_locale(
            selected, output, MSYS_I18N_LOCALE_CAPACITY
        );
    }
    return MSYS_I18N_OK;
}

static const char *lookup_keys(
    const msys_i18n_catalog *catalog,
    const char *locale,
    const char *first_key,
    const char *second_key
)
{
    char candidate[MSYS_I18N_LOCALE_CAPACITY];
    const char *value;

    if (catalog == NULL || catalog->default_locale == NULL ||
        catalog->entries == NULL || first_key == NULL || *first_key == '\0' ||
        requested_locale(catalog, locale, candidate) != MSYS_I18N_OK) {
        return NULL;
    }
    for (;;) {
        value = lookup_exact(catalog, candidate, first_key);
        if (value == NULL && second_key != NULL) {
            value = lookup_exact(catalog, candidate, second_key);
        }
        if (value != NULL) {
            return value;
        }
        {
            char *separator = strrchr(candidate, '-');
            if (separator == NULL) {
                break;
            }
            *separator = '\0';
        }
    }
    value = lookup_exact(catalog, catalog->default_locale, first_key);
    if (value == NULL && second_key != NULL) {
        value = lookup_exact(catalog, catalog->default_locale, second_key);
    }
    return value;
}

const char *msys_i18n_lookup(
    const msys_i18n_catalog *catalog,
    const char *locale,
    const char *key
)
{
    return lookup_keys(catalog, locale, key, NULL);
}

static uint64_t magnitude(int64_t count)
{
    return count < 0 ? (uint64_t)(-(count + 1)) + UINT64_C(1) : (uint64_t)count;
}

static int language_is(const char *locale, const char *language)
{
    size_t length = strlen(language);
    return strncmp(locale, language, length) == 0 &&
        (locale[length] == '\0' || locale[length] == '-' || locale[length] == '_');
}

const char *msys_i18n_plural_category(const char *locale, int64_t count)
{
    char canonical[MSYS_I18N_LOCALE_CAPACITY];
    uint64_t number = magnitude(count);
    uint64_t mod10 = number % UINT64_C(10);
    uint64_t mod100 = number % UINT64_C(100);
    const char *selected = "en";

    if (locale != NULL &&
        msys_i18n_normalize_locale(locale, canonical, sizeof(canonical)) == MSYS_I18N_OK) {
        selected = canonical;
    }

    if (language_is(selected, "zh") || language_is(selected, "ja") ||
        language_is(selected, "ko") || language_is(selected, "th") ||
        language_is(selected, "vi") || language_is(selected, "id") ||
        language_is(selected, "ms")) {
        return "other";
    }
    if (language_is(selected, "ar")) {
        if (number == 0u) return "zero";
        if (number == 1u) return "one";
        if (number == 2u) return "two";
        if (mod100 >= 3u && mod100 <= 10u) return "few";
        if (mod100 >= 11u && mod100 <= 99u) return "many";
        return "other";
    }
    if (language_is(selected, "ru") || language_is(selected, "uk") ||
        language_is(selected, "be")) {
        if (mod10 == 1u && mod100 != 11u) return "one";
        if (mod10 >= 2u && mod10 <= 4u && !(mod100 >= 12u && mod100 <= 14u))
            return "few";
        return "many";
    }
    if (language_is(selected, "pl")) {
        if (number == 1u) return "one";
        if (mod10 >= 2u && mod10 <= 4u && !(mod100 >= 12u && mod100 <= 14u))
            return "few";
        return "many";
    }
    if (language_is(selected, "cs") || language_is(selected, "sk")) {
        if (number == 1u) return "one";
        if (number >= 2u && number <= 4u) return "few";
        return "other";
    }
    if (language_is(selected, "sl")) {
        if (mod100 == 1u) return "one";
        if (mod100 == 2u) return "two";
        if (mod100 == 3u || mod100 == 4u) return "few";
        return "other";
    }
    if (language_is(selected, "lt")) {
        if (mod10 == 1u && mod100 != 11u) return "one";
        if (mod10 >= 2u && mod10 <= 9u && !(mod100 >= 11u && mod100 <= 19u))
            return "few";
        return "other";
    }
    if (language_is(selected, "lv")) {
        if (mod10 == 0u || (mod100 >= 11u && mod100 <= 19u)) return "zero";
        if (mod10 == 1u && mod100 != 11u) return "one";
        return "other";
    }
    if (language_is(selected, "ro")) {
        if (number == 1u) return "one";
        if (number == 0u || (mod100 >= 1u && mod100 <= 19u)) return "few";
        return "other";
    }
    if (language_is(selected, "he")) {
        if (number == 1u) return "one";
        if (number == 2u) return "two";
        if (number != 0u && mod10 == 0u) return "many";
        return "other";
    }
    if (language_is(selected, "cy")) {
        if (number == 0u) return "zero";
        if (number == 1u) return "one";
        if (number == 2u) return "two";
        if (number == 3u) return "few";
        if (number == 6u) return "many";
        return "other";
    }
    if (language_is(selected, "ga")) {
        if (number == 1u) return "one";
        if (number == 2u) return "two";
        if (number >= 3u && number <= 6u) return "few";
        if (number >= 7u && number <= 10u) return "many";
        return "other";
    }
    if (language_is(selected, "fr") || language_is(selected, "hi") ||
        (language_is(selected, "pt") && strcmp(selected, "pt-PT") != 0)) {
        return number <= 1u ? "one" : "other";
    }
    if (language_is(selected, "is") || language_is(selected, "mk")) {
        return mod10 == 1u && mod100 != 11u ? "one" : "other";
    }
    return number == 1u ? "one" : "other";
}

static const char *lookup_plural_exact(
    const msys_i18n_catalog *catalog,
    const char *locale,
    const char *key,
    int64_t count
)
{
    char category_key[256];
    char other_key[256];
    const char *category;
    const char *value;
    int category_length;
    int other_length;

    category = msys_i18n_plural_category(locale, count);
    category_length = snprintf(category_key, sizeof(category_key), "%s.%s", key, category);
    other_length = snprintf(other_key, sizeof(other_key), "%s.other", key);
    if (category_length < 0 || other_length < 0 ||
        (size_t)category_length >= sizeof(category_key) ||
        (size_t)other_length >= sizeof(other_key)) {
        return NULL;
    }
    value = lookup_exact(catalog, locale, category_key);
    return value == NULL ? lookup_exact(catalog, locale, other_key) : value;
}

const char *msys_i18n_lookup_plural(
    const msys_i18n_catalog *catalog,
    const char *locale,
    const char *key,
    int64_t count
)
{
    char candidate[MSYS_I18N_LOCALE_CAPACITY];
    const char *value;

    if (catalog == NULL || key == NULL || *key == '\0' ||
        requested_locale(catalog, locale, candidate) != MSYS_I18N_OK) {
        return NULL;
    }
    for (;;) {
        value = lookup_plural_exact(catalog, candidate, key, count);
        if (value != NULL) {
            return value;
        }
        {
            char *separator = strrchr(candidate, '-');
            if (separator == NULL) {
                break;
            }
            *separator = '\0';
        }
    }
    value = lookup_plural_exact(catalog, catalog->default_locale, key, count);
    return value == NULL ? msys_i18n_lookup(catalog, locale, key) : value;
}

static const char *find_param(
    const msys_i18n_param *params,
    size_t count,
    const char *name,
    size_t name_length
)
{
    size_t index;
    for (index = 0u; index < count; ++index) {
        if (params[index].name != NULL && params[index].value != NULL &&
            strlen(params[index].name) == name_length &&
            strncmp(params[index].name, name, name_length) == 0) {
            return params[index].value;
        }
    }
    return NULL;
}

static void append_bytes(
    char *output,
    size_t capacity,
    size_t *length,
    const char *value,
    size_t value_length
)
{
    if (output != NULL && capacity > 0u && *length < capacity - 1u) {
        size_t available = capacity - 1u - *length;
        size_t copied = value_length < available ? value_length : available;
        memcpy(output + *length, value, copied);
    }
    *length += value_length;
}

int msys_i18n_render(
    const char *message,
    const msys_i18n_param *params,
    size_t param_count,
    char *output,
    size_t capacity,
    size_t *required
)
{
    size_t index = 0u;
    size_t length = 0u;
    int invalid = 0;

    if (message == NULL || (param_count != 0u && params == NULL) ||
        (capacity != 0u && output == NULL)) {
        return MSYS_I18N_INVALID_ARGUMENT;
    }
    while (message[index] != '\0') {
        if (message[index] == '{' && message[index + 1u] == '{') {
            append_bytes(output, capacity, &length, "{", 1u);
            index += 2u;
        } else if (message[index] == '}' && message[index + 1u] == '}') {
            append_bytes(output, capacity, &length, "}", 1u);
            index += 2u;
        } else if (message[index] == '{') {
            size_t begin = ++index;
            const char *value;
            while (message[index] != '\0' && message[index] != '}') {
                unsigned char byte = (unsigned char)message[index];
                if ((index == begin && !(isalpha(byte) || byte == '_')) ||
                    (index != begin && !(isalnum(byte) || byte == '_'))) {
                    invalid = 1;
                }
                ++index;
            }
            if (message[index] != '}' || index == begin || invalid != 0) {
                invalid = 1;
                break;
            }
            value = find_param(params, param_count, message + begin, index - begin);
            if (value == NULL) {
                append_bytes(output, capacity, &length, "{", 1u);
                append_bytes(output, capacity, &length, message + begin, index - begin);
                append_bytes(output, capacity, &length, "}", 1u);
            } else {
                append_bytes(output, capacity, &length, value, strlen(value));
            }
            ++index;
        } else if (message[index] == '}') {
            invalid = 1;
            break;
        } else {
            append_bytes(output, capacity, &length, message + index, 1u);
            ++index;
        }
    }
    if (required != NULL) {
        *required = length;
    }
    if (output != NULL && capacity > 0u) {
        output[length < capacity ? length : capacity - 1u] = '\0';
    }
    if (invalid != 0) {
        return MSYS_I18N_INVALID_TEMPLATE;
    }
    return length + 1u > capacity ? MSYS_I18N_BUFFER_TOO_SMALL : MSYS_I18N_OK;
}
