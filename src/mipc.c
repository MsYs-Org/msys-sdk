#define _POSIX_C_SOURCE 200809L

#include "msys/mipc.h"

#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

#define MSYS_JSON_MAX_DEPTH 64u

static int set_result(msys_mipc_client *client, int result, int saved_errno)
{
    if (client != NULL) {
        client->last_errno = saved_errno;
    }
    return result;
}

static int validate_client(msys_mipc_client *client)
{
    if (client == NULL || client->fd < 0) {
        return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, 0);
    }
    return MSYS_MIPC_OK;
}

static int validate_socket(int fd, int *saved_errno)
{
    int socket_type = 0;
    socklen_t socket_type_len = (socklen_t)sizeof(socket_type);
    struct sockaddr_storage address;
    socklen_t address_len = (socklen_t)sizeof(address);

    if (fd < 0) {
        *saved_errno = EBADF;
        return MSYS_MIPC_INVALID_FD;
    }
    if (getsockopt(fd, SOL_SOCKET, SO_TYPE, &socket_type, &socket_type_len) < 0) {
        *saved_errno = errno;
        return MSYS_MIPC_INVALID_FD;
    }
    if (socket_type != SOCK_SEQPACKET) {
        *saved_errno = EPROTOTYPE;
        return MSYS_MIPC_INVALID_FD;
    }
    memset(&address, 0, sizeof(address));
    if (getsockname(fd, (struct sockaddr *)&address, &address_len) < 0) {
        *saved_errno = errno;
        return MSYS_MIPC_INVALID_FD;
    }
    if (address.ss_family != AF_UNIX) {
        *saved_errno = EAFNOSUPPORT;
        return MSYS_MIPC_INVALID_FD;
    }
    *saved_errno = 0;
    return MSYS_MIPC_OK;
}

int msys_mipc_client_init(msys_mipc_client *client, int fd, int take_ownership)
{
    int saved_errno = 0;
    int result;

    if (client == NULL) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    client->fd = -1;
    client->owns_fd = 0;
    client->last_errno = 0;

    result = validate_socket(fd, &saved_errno);
    if (result != MSYS_MIPC_OK) {
        return set_result(client, result, saved_errno);
    }
    client->fd = fd;
    client->owns_fd = take_ownership != 0;
    return MSYS_MIPC_OK;
}

int msys_mipc_client_from_env(msys_mipc_client *client)
{
    const char *text;
    char *end = NULL;
    long fd_value;

    if (client == NULL) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    client->fd = -1;
    client->owns_fd = 0;
    client->last_errno = 0;

    text = getenv("MSYS_CONTROL_FD");
    if (text == NULL || *text == '\0') {
        return set_result(client, MSYS_MIPC_INVALID_FD, ENOENT);
    }
    errno = 0;
    fd_value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || fd_value < 0 || fd_value > INT_MAX) {
        return set_result(client, MSYS_MIPC_INVALID_FD, EINVAL);
    }
    return msys_mipc_client_init(client, (int)fd_value, 0);
}

void msys_mipc_client_close(msys_mipc_client *client)
{
    if (client == NULL) {
        return;
    }
    if (client->fd >= 0 && client->owns_fd != 0) {
        (void)close(client->fd);
    }
    client->fd = -1;
    client->owns_fd = 0;
    client->last_errno = 0;
}

int msys_mipc_client_fd(const msys_mipc_client *client)
{
    return client == NULL ? -1 : client->fd;
}

int msys_mipc_last_errno(const msys_mipc_client *client)
{
    return client == NULL ? 0 : client->last_errno;
}

const char *msys_mipc_result_string(int result)
{
    switch (result) {
    case MSYS_MIPC_OK:
        return "ok";
    case MSYS_MIPC_TIMEOUT:
        return "timeout";
    case MSYS_MIPC_EOF:
        return "end of stream";
    case MSYS_MIPC_NOT_FOUND:
        return "field not found";
    case MSYS_MIPC_INVALID_ARGUMENT:
        return "invalid argument";
    case MSYS_MIPC_INVALID_FD:
        return "invalid mIPC descriptor";
    case MSYS_MIPC_IO_ERROR:
        return "I/O error";
    case MSYS_MIPC_TOO_LARGE:
        return "packet too large";
    case MSYS_MIPC_BUFFER_TOO_SMALL:
        return "buffer too small";
    case MSYS_MIPC_INVALID_JSON:
        return "invalid JSON";
    default:
        return "unknown result";
    }
}

static const char *skip_space(const char *cursor)
{
    while (*cursor != '\0' && isspace((unsigned char)*cursor) != 0) {
        ++cursor;
    }
    return cursor;
}

static int json_object_shape(const char *json, size_t length)
{
    size_t begin = 0;
    size_t end = length;

    while (begin < end && isspace((unsigned char)json[begin]) != 0) {
        ++begin;
    }
    while (end > begin && isspace((unsigned char)json[end - 1u]) != 0) {
        --end;
    }
    return end - begin >= 2u && json[begin] == '{' && json[end - 1u] == '}';
}

int msys_mipc_send_json(msys_mipc_client *client, const char *json_object)
{
    size_t length;
    ssize_t sent;
    int result = validate_client(client);

    if (result != MSYS_MIPC_OK) {
        return result;
    }
    if (json_object == NULL) {
        return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, 0);
    }
    length = strlen(json_object);
    if (length == 0u || json_object_shape(json_object, length) == 0) {
        return set_result(client, MSYS_MIPC_INVALID_JSON, 0);
    }
    if (length > MSYS_MIPC_MAX_PACKET) {
        return set_result(client, MSYS_MIPC_TOO_LARGE, 0);
    }

    do {
        sent = send(client->fd, json_object, length, MSG_NOSIGNAL);
    } while (sent < 0 && errno == EINTR);

    if (sent < 0) {
        return set_result(client, MSYS_MIPC_IO_ERROR, errno);
    }
    if ((size_t)sent != length) {
        return set_result(client, MSYS_MIPC_IO_ERROR, EIO);
    }
    return set_result(client, MSYS_MIPC_OK, 0);
}

static int escaped_json_string(const char *input, char **escaped)
{
    static const char hex[] = "0123456789abcdef";
    size_t input_length;
    size_t output_length = 0;
    size_t index;
    char *output;
    char *write_cursor;

    if (input == NULL || escaped == NULL) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    input_length = strlen(input);
    if (input_length > MSYS_MIPC_MAX_PACKET) {
        return MSYS_MIPC_TOO_LARGE;
    }
    for (index = 0; index < input_length; ++index) {
        unsigned char byte = (unsigned char)input[index];
        size_t addition = 1u;
        if (byte == '"' || byte == '\\' || byte == '\b' || byte == '\f' ||
            byte == '\n' || byte == '\r' || byte == '\t') {
            addition = 2u;
        } else if (byte < 0x20u) {
            addition = 6u;
        }
        if (output_length > MSYS_MIPC_MAX_PACKET - addition) {
            return MSYS_MIPC_TOO_LARGE;
        }
        output_length += addition;
    }
    output = (char *)malloc(output_length + 1u);
    if (output == NULL) {
        return MSYS_MIPC_IO_ERROR;
    }
    write_cursor = output;
    for (index = 0; index < input_length; ++index) {
        unsigned char byte = (unsigned char)input[index];
        switch (byte) {
        case '"':
            *write_cursor++ = '\\';
            *write_cursor++ = '"';
            break;
        case '\\':
            *write_cursor++ = '\\';
            *write_cursor++ = '\\';
            break;
        case '\b':
            *write_cursor++ = '\\';
            *write_cursor++ = 'b';
            break;
        case '\f':
            *write_cursor++ = '\\';
            *write_cursor++ = 'f';
            break;
        case '\n':
            *write_cursor++ = '\\';
            *write_cursor++ = 'n';
            break;
        case '\r':
            *write_cursor++ = '\\';
            *write_cursor++ = 'r';
            break;
        case '\t':
            *write_cursor++ = '\\';
            *write_cursor++ = 't';
            break;
        default:
            if (byte < 0x20u) {
                *write_cursor++ = '\\';
                *write_cursor++ = 'u';
                *write_cursor++ = '0';
                *write_cursor++ = '0';
                *write_cursor++ = hex[(byte >> 4u) & 0x0fu];
                *write_cursor++ = hex[byte & 0x0fu];
            } else {
                *write_cursor++ = (char)byte;
            }
            break;
        }
    }
    *write_cursor = '\0';
    *escaped = output;
    return MSYS_MIPC_OK;
}

static int send_format(msys_mipc_client *client, const char *format, ...)
{
    va_list arguments;
    va_list copy;
    int required;
    char *json;
    int result;

    if (client == NULL || format == NULL) {
        return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, 0);
    }
    va_start(arguments, format);
    va_copy(copy, arguments);
    required = vsnprintf(NULL, 0, format, copy);
    va_end(copy);
    if (required < 0) {
        va_end(arguments);
        return set_result(client, MSYS_MIPC_IO_ERROR, EILSEQ);
    }
    if ((size_t)required > MSYS_MIPC_MAX_PACKET) {
        va_end(arguments);
        return set_result(client, MSYS_MIPC_TOO_LARGE, 0);
    }
    json = (char *)malloc((size_t)required + 1u);
    if (json == NULL) {
        va_end(arguments);
        return set_result(client, MSYS_MIPC_IO_ERROR, ENOMEM);
    }
    if (vsnprintf(json, (size_t)required + 1u, format, arguments) != required) {
        free(json);
        va_end(arguments);
        return set_result(client, MSYS_MIPC_IO_ERROR, EILSEQ);
    }
    va_end(arguments);
    result = msys_mipc_send_json(client, json);
    free(json);
    return result;
}

int msys_mipc_send_hello(
    msys_mipc_client *client,
    const char *component_id,
    uint64_t generation
)
{
    char *component = NULL;
    int result = escaped_json_string(component_id, &component);
    if (result != MSYS_MIPC_OK) {
        return set_result(client, result, result == MSYS_MIPC_IO_ERROR ? ENOMEM : 0);
    }
    result = send_format(
        client,
        "{\"type\":\"hello\",\"component\":\"%s\",\"generation\":%" PRIu64 "}",
        component,
        generation
    );
    free(component);
    return result;
}

int msys_mipc_send_hello_from_env(msys_mipc_client *client)
{
    const char *component_id = getenv("MSYS_COMPONENT_ID");
    const char *generation_text = getenv("MSYS_GENERATION");
    char *end = NULL;
    uint64_t generation = 0;
    unsigned long long parsed;

    if (component_id == NULL || *component_id == '\0') {
        component_id = "unknown";
    }
    if (generation_text != NULL && *generation_text != '\0') {
        if (*generation_text == '-') {
            return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, EINVAL);
        }
        errno = 0;
        parsed = strtoull(generation_text, &end, 10);
        if (errno != 0 || end == generation_text || *end != '\0') {
            return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, EINVAL);
        }
        generation = (uint64_t)parsed;
    }
    return msys_mipc_send_hello(client, component_id, generation);
}

int msys_mipc_send_ready(msys_mipc_client *client)
{
    return msys_mipc_send_json(client, "{\"type\":\"ready\"}");
}

int msys_mipc_send_subscribe(msys_mipc_client *client, const char *topic)
{
    char *escaped_topic = NULL;
    int result = escaped_json_string(topic, &escaped_topic);
    if (result != MSYS_MIPC_OK) {
        return set_result(client, result, result == MSYS_MIPC_IO_ERROR ? ENOMEM : 0);
    }
    result = send_format(client, "{\"type\":\"subscribe\",\"topic\":\"%s\"}", escaped_topic);
    free(escaped_topic);
    return result;
}

static int validate_raw_value(msys_mipc_client *client, const char *json)
{
    const char *begin;
    size_t length;

    if (json == NULL) {
        return MSYS_MIPC_OK;
    }
    length = strlen(json);
    if (length > MSYS_MIPC_MAX_PACKET) {
        return set_result(client, MSYS_MIPC_TOO_LARGE, 0);
    }
    begin = skip_space(json);
    if (*begin == '\0') {
        return set_result(client, MSYS_MIPC_INVALID_JSON, 0);
    }
    return MSYS_MIPC_OK;
}

int msys_mipc_monotonic_ms(uint64_t *value)
{
    struct timespec now;
    uint64_t seconds;
    uint64_t milliseconds;

    if (value == NULL) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0 || now.tv_sec < 0 || now.tv_nsec < 0) {
        return MSYS_MIPC_IO_ERROR;
    }
    seconds = (uint64_t)now.tv_sec;
    if (seconds > UINT64_MAX / UINT64_C(1000)) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    milliseconds = seconds * UINT64_C(1000);
    milliseconds += (uint64_t)now.tv_nsec / UINT64_C(1000000);
    *value = milliseconds;
    return MSYS_MIPC_OK;
}

int msys_mipc_send_call_json(
    msys_mipc_client *client,
    uint64_t request_id,
    const char *target,
    const char *method,
    const char *payload_json,
    uint64_t deadline_ms,
    int idempotent
)
{
    char *escaped_target = NULL;
    char *escaped_method = NULL;
    const char *payload = payload_json == NULL ? "{}" : payload_json;
    int result = validate_raw_value(client, payload);

    if (result != MSYS_MIPC_OK) {
        return result;
    }
    if (target == NULL || *target == '\0' || method == NULL || *method == '\0') {
        return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, 0);
    }
    result = escaped_json_string(target, &escaped_target);
    if (result != MSYS_MIPC_OK) {
        return set_result(client, result, result == MSYS_MIPC_IO_ERROR ? ENOMEM : 0);
    }
    result = escaped_json_string(method, &escaped_method);
    if (result != MSYS_MIPC_OK) {
        free(escaped_target);
        return set_result(client, result, result == MSYS_MIPC_IO_ERROR ? ENOMEM : 0);
    }
    result = send_format(
        client,
        "{\"type\":\"call\",\"id\":%" PRIu64
        ",\"target\":\"%s\",\"method\":\"%s\",\"payload\":%s"
        ",\"deadline_ms\":%" PRIu64 ",\"idempotent\":%s}",
        request_id,
        escaped_target,
        escaped_method,
        payload,
        deadline_ms,
        idempotent != 0 ? "true" : "false"
    );
    free(escaped_target);
    free(escaped_method);
    return result;
}

int msys_mipc_send_event_json(
    msys_mipc_client *client,
    const char *topic,
    const char *payload_json
)
{
    char *escaped_topic = NULL;
    const char *payload = payload_json == NULL ? "{}" : payload_json;
    int result = validate_raw_value(client, payload);
    if (result != MSYS_MIPC_OK) {
        return result;
    }
    result = escaped_json_string(topic, &escaped_topic);
    if (result != MSYS_MIPC_OK) {
        return set_result(client, result, result == MSYS_MIPC_IO_ERROR ? ENOMEM : 0);
    }
    result = send_format(
        client,
        "{\"type\":\"event\",\"topic\":\"%s\",\"payload\":%s}",
        escaped_topic,
        payload
    );
    free(escaped_topic);
    return result;
}

int msys_mipc_send_return_json(
    msys_mipc_client *client,
    uint64_t request_id,
    const char *payload_json
)
{
    const char *payload = payload_json == NULL ? "{}" : payload_json;
    int result = validate_raw_value(client, payload);
    if (result != MSYS_MIPC_OK) {
        return result;
    }
    return send_format(
        client,
        "{\"type\":\"return\",\"id\":%" PRIu64 ",\"payload\":%s}",
        request_id,
        payload
    );
}

int msys_mipc_send_error(
    msys_mipc_client *client,
    uint64_t request_id,
    const char *code,
    const char *message
)
{
    char *escaped_code = NULL;
    char *escaped_message = NULL;
    int result = escaped_json_string(code == NULL ? "ERROR" : code, &escaped_code);
    if (result != MSYS_MIPC_OK) {
        return set_result(client, result, result == MSYS_MIPC_IO_ERROR ? ENOMEM : 0);
    }
    result = escaped_json_string(message == NULL ? "" : message, &escaped_message);
    if (result != MSYS_MIPC_OK) {
        free(escaped_code);
        return set_result(client, result, result == MSYS_MIPC_IO_ERROR ? ENOMEM : 0);
    }
    result = send_format(
        client,
        "{\"type\":\"error\",\"id\":%" PRIu64
        ",\"code\":\"%s\",\"message\":\"%s\"}",
        request_id,
        escaped_code,
        escaped_message
    );
    free(escaped_code);
    free(escaped_message);
    return result;
}

static int wait_for_packet(msys_mipc_client *client, int timeout_ms)
{
    struct pollfd descriptor;
    int polled;

    if (timeout_ms < -1) {
        return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, 0);
    }
    descriptor.fd = client->fd;
    descriptor.events = POLLIN;
    descriptor.revents = 0;
    do {
        polled = poll(&descriptor, 1, timeout_ms);
    } while (polled < 0 && errno == EINTR);
    if (polled == 0) {
        return set_result(client, MSYS_MIPC_TIMEOUT, 0);
    }
    if (polled < 0) {
        return set_result(client, MSYS_MIPC_IO_ERROR, errno);
    }
    if ((descriptor.revents & (POLLIN | POLLHUP)) == 0) {
        return set_result(client, MSYS_MIPC_IO_ERROR, EIO);
    }
    return MSYS_MIPC_OK;
}

int msys_mipc_recv_json(
    msys_mipc_client *client,
    char *buffer,
    size_t capacity,
    int timeout_ms,
    size_t *json_len
)
{
    char probe = '\0';
    ssize_t packet_length;
    ssize_t received;
    int result = validate_client(client);

    if (json_len != NULL) {
        *json_len = 0u;
    }
    if (result != MSYS_MIPC_OK) {
        return result;
    }
    if (buffer == NULL || capacity == 0u) {
        return set_result(client, MSYS_MIPC_INVALID_ARGUMENT, 0);
    }
    buffer[0] = '\0';
    result = wait_for_packet(client, timeout_ms);
    if (result != MSYS_MIPC_OK) {
        return result;
    }

    do {
        packet_length = recv(client->fd, &probe, 1u, MSG_PEEK | MSG_TRUNC);
    } while (packet_length < 0 && errno == EINTR);
    if (packet_length < 0) {
        return set_result(client, MSYS_MIPC_IO_ERROR, errno);
    }
    if (packet_length == 0) {
        return set_result(client, MSYS_MIPC_EOF, 0);
    }
    if (json_len != NULL) {
        *json_len = (size_t)packet_length;
    }
    if ((size_t)packet_length > MSYS_MIPC_MAX_PACKET) {
        do {
            received = recv(client->fd, &probe, 1u, 0);
        } while (received < 0 && errno == EINTR);
        if (received < 0) {
            return set_result(client, MSYS_MIPC_IO_ERROR, errno);
        }
        return set_result(client, MSYS_MIPC_TOO_LARGE, 0);
    }
    if ((size_t)packet_length >= capacity) {
        return set_result(client, MSYS_MIPC_BUFFER_TOO_SMALL, 0);
    }

    do {
        received = recv(client->fd, buffer, (size_t)packet_length, 0);
    } while (received < 0 && errno == EINTR);
    if (received < 0) {
        buffer[0] = '\0';
        return set_result(client, MSYS_MIPC_IO_ERROR, errno);
    }
    if (received != packet_length) {
        buffer[0] = '\0';
        return set_result(client, MSYS_MIPC_IO_ERROR, EIO);
    }
    buffer[(size_t)received] = '\0';
    if (json_object_shape(buffer, (size_t)received) == 0) {
        return set_result(client, MSYS_MIPC_INVALID_JSON, 0);
    }
    return set_result(client, MSYS_MIPC_OK, 0);
}

static int hex_value(char character)
{
    if (character >= '0' && character <= '9') {
        return character - '0';
    }
    if (character >= 'a' && character <= 'f') {
        return character - 'a' + 10;
    }
    if (character >= 'A' && character <= 'F') {
        return character - 'A' + 10;
    }
    return -1;
}

static const char *skip_json_string(const char *cursor)
{
    if (*cursor != '"') {
        return NULL;
    }
    ++cursor;
    while (*cursor != '\0') {
        unsigned char byte = (unsigned char)*cursor++;
        if (byte == '"') {
            return cursor;
        }
        if (byte < 0x20u) {
            return NULL;
        }
        if (byte == '\\') {
            char escape = *cursor++;
            unsigned index;
            if (escape == '\0') {
                return NULL;
            }
            if (escape == 'u') {
                for (index = 0; index < 4u; ++index) {
                    if (*cursor == '\0' || hex_value(*cursor) < 0) {
                        return NULL;
                    }
                    ++cursor;
                }
            } else if (strchr("\"\\/bfnrt", escape) == NULL) {
                return NULL;
            }
        }
    }
    return NULL;
}

static const char *skip_json_value(const char *cursor, unsigned depth)
{
    const char *next;

    if (depth > MSYS_JSON_MAX_DEPTH) {
        return NULL;
    }
    cursor = skip_space(cursor);
    if (*cursor == '"') {
        return skip_json_string(cursor);
    }
    if (*cursor == '{') {
        cursor = skip_space(cursor + 1);
        if (*cursor == '}') {
            return cursor + 1;
        }
        for (;;) {
            next = skip_json_string(cursor);
            if (next == NULL) {
                return NULL;
            }
            cursor = skip_space(next);
            if (*cursor != ':') {
                return NULL;
            }
            cursor = skip_json_value(cursor + 1, depth + 1u);
            if (cursor == NULL) {
                return NULL;
            }
            cursor = skip_space(cursor);
            if (*cursor == '}') {
                return cursor + 1;
            }
            if (*cursor != ',') {
                return NULL;
            }
            cursor = skip_space(cursor + 1);
        }
    }
    if (*cursor == '[') {
        cursor = skip_space(cursor + 1);
        if (*cursor == ']') {
            return cursor + 1;
        }
        for (;;) {
            cursor = skip_json_value(cursor, depth + 1u);
            if (cursor == NULL) {
                return NULL;
            }
            cursor = skip_space(cursor);
            if (*cursor == ']') {
                return cursor + 1;
            }
            if (*cursor != ',') {
                return NULL;
            }
            cursor = skip_space(cursor + 1);
        }
    }
    if (strncmp(cursor, "true", 4u) == 0) {
        return cursor + 4;
    }
    if (strncmp(cursor, "false", 5u) == 0) {
        return cursor + 5;
    }
    if (strncmp(cursor, "null", 4u) == 0) {
        return cursor + 4;
    }
    if (*cursor == '-' || isdigit((unsigned char)*cursor) != 0) {
        const char *number = cursor;
        if (*cursor == '-') {
            ++cursor;
        }
        if (*cursor == '0') {
            ++cursor;
        } else {
            if (isdigit((unsigned char)*cursor) == 0) {
                return NULL;
            }
            while (isdigit((unsigned char)*cursor) != 0) {
                ++cursor;
            }
        }
        if (*cursor == '.') {
            ++cursor;
            if (isdigit((unsigned char)*cursor) == 0) {
                return NULL;
            }
            while (isdigit((unsigned char)*cursor) != 0) {
                ++cursor;
            }
        }
        if (*cursor == 'e' || *cursor == 'E') {
            ++cursor;
            if (*cursor == '+' || *cursor == '-') {
                ++cursor;
            }
            if (isdigit((unsigned char)*cursor) == 0) {
                return NULL;
            }
            while (isdigit((unsigned char)*cursor) != 0) {
                ++cursor;
            }
        }
        return cursor == number ? NULL : cursor;
    }
    return NULL;
}

static int json_key_equals(const char *begin, const char *end, const char *key)
{
    const char *cursor = begin + 1;
    const char *key_cursor = key;

    while (cursor < end - 1) {
        unsigned char decoded;
        if (*cursor == '\\') {
            char escape;
            ++cursor;
            if (cursor >= end - 1) {
                return 0;
            }
            escape = *cursor++;
            switch (escape) {
            case '"': decoded = '"'; break;
            case '\\': decoded = '\\'; break;
            case '/': decoded = '/'; break;
            case 'b': decoded = '\b'; break;
            case 'f': decoded = '\f'; break;
            case 'n': decoded = '\n'; break;
            case 'r': decoded = '\r'; break;
            case 't': decoded = '\t'; break;
            default:
                return 0;
            }
        } else {
            decoded = (unsigned char)*cursor++;
        }
        if (*key_cursor == '\0' || decoded != (unsigned char)*key_cursor++) {
            return 0;
        }
    }
    return *key_cursor == '\0';
}

static int find_top_level_value(
    const char *json,
    const char *key,
    const char **value_begin,
    const char **value_end
)
{
    const char *cursor;
    const char *key_end;
    const char *end;

    if (json == NULL || key == NULL || *key == '\0' || value_begin == NULL || value_end == NULL) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    cursor = skip_space(json);
    if (*cursor != '{') {
        return MSYS_MIPC_INVALID_JSON;
    }
    cursor = skip_space(cursor + 1);
    if (*cursor == '}') {
        return MSYS_MIPC_NOT_FOUND;
    }
    for (;;) {
        const char *key_begin = cursor;
        key_end = skip_json_string(key_begin);
        if (key_end == NULL) {
            return MSYS_MIPC_INVALID_JSON;
        }
        cursor = skip_space(key_end);
        if (*cursor != ':') {
            return MSYS_MIPC_INVALID_JSON;
        }
        cursor = skip_space(cursor + 1);
        end = skip_json_value(cursor, 1u);
        if (end == NULL) {
            return MSYS_MIPC_INVALID_JSON;
        }
        if (*skip_space(end) != ',' && *skip_space(end) != '}') {
            return MSYS_MIPC_INVALID_JSON;
        }
        if (json_key_equals(key_begin, key_end, key) != 0) {
            *value_begin = cursor;
            *value_end = end;
            return MSYS_MIPC_OK;
        }
        cursor = skip_space(end);
        if (*cursor == '}') {
            return MSYS_MIPC_NOT_FOUND;
        }
        if (*cursor != ',') {
            return MSYS_MIPC_INVALID_JSON;
        }
        cursor = skip_space(cursor + 1);
    }
}

int msys_mipc_json_get_raw(
    const char *json,
    const char *key,
    const char **raw_value,
    size_t *raw_length
)
{
    const char *begin;
    const char *end;
    int result;

    if (raw_value == NULL || raw_length == NULL) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    *raw_value = NULL;
    *raw_length = 0u;
    result = find_top_level_value(json, key, &begin, &end);
    if (result != MSYS_MIPC_OK) {
        return result;
    }
    *raw_value = begin;
    *raw_length = (size_t)(end - begin);
    return MSYS_MIPC_OK;
}

static int decode_hex_quad(const char *cursor, uint32_t *value)
{
    uint32_t result = 0;
    unsigned index;
    for (index = 0; index < 4u; ++index) {
        int digit = hex_value(cursor[index]);
        if (digit < 0) {
            return MSYS_MIPC_INVALID_JSON;
        }
        result = (result << 4u) | (uint32_t)digit;
    }
    *value = result;
    return MSYS_MIPC_OK;
}

static size_t encode_utf8(uint32_t codepoint, unsigned char output[4])
{
    if (codepoint <= 0x7fu) {
        output[0] = (unsigned char)codepoint;
        return 1u;
    }
    if (codepoint <= 0x7ffu) {
        output[0] = (unsigned char)(0xc0u | (codepoint >> 6u));
        output[1] = (unsigned char)(0x80u | (codepoint & 0x3fu));
        return 2u;
    }
    if (codepoint <= 0xffffu) {
        output[0] = (unsigned char)(0xe0u | (codepoint >> 12u));
        output[1] = (unsigned char)(0x80u | ((codepoint >> 6u) & 0x3fu));
        output[2] = (unsigned char)(0x80u | (codepoint & 0x3fu));
        return 3u;
    }
    output[0] = (unsigned char)(0xf0u | (codepoint >> 18u));
    output[1] = (unsigned char)(0x80u | ((codepoint >> 12u) & 0x3fu));
    output[2] = (unsigned char)(0x80u | ((codepoint >> 6u) & 0x3fu));
    output[3] = (unsigned char)(0x80u | (codepoint & 0x3fu));
    return 4u;
}

int msys_mipc_json_get_string(
    const char *json,
    const char *key,
    char *buffer,
    size_t capacity,
    size_t *string_length
)
{
    const char *cursor;
    const char *end;
    size_t written = 0u;
    int result;

    if (string_length != NULL) {
        *string_length = 0u;
    }
    if (buffer == NULL || capacity == 0u) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    buffer[0] = '\0';
    result = find_top_level_value(json, key, &cursor, &end);
    if (result != MSYS_MIPC_OK) {
        return result;
    }
    if (*cursor != '"' || end <= cursor + 1 || end[-1] != '"') {
        return MSYS_MIPC_INVALID_JSON;
    }
    ++cursor;
    --end;
    while (cursor < end) {
        unsigned char bytes[4];
        size_t byte_count = 1u;

        if (*cursor != '\\') {
            bytes[0] = (unsigned char)*cursor++;
        } else {
            char escape;
            ++cursor;
            if (cursor >= end) {
                return MSYS_MIPC_INVALID_JSON;
            }
            escape = *cursor++;
            switch (escape) {
            case '"': bytes[0] = '"'; break;
            case '\\': bytes[0] = '\\'; break;
            case '/': bytes[0] = '/'; break;
            case 'b': bytes[0] = '\b'; break;
            case 'f': bytes[0] = '\f'; break;
            case 'n': bytes[0] = '\n'; break;
            case 'r': bytes[0] = '\r'; break;
            case 't': bytes[0] = '\t'; break;
            case 'u': {
                uint32_t codepoint;
                uint32_t low_surrogate;
                if ((size_t)(end - cursor) < 4u || decode_hex_quad(cursor, &codepoint) != MSYS_MIPC_OK) {
                    return MSYS_MIPC_INVALID_JSON;
                }
                cursor += 4;
                if (codepoint >= 0xd800u && codepoint <= 0xdbffu) {
                    if ((size_t)(end - cursor) < 6u || cursor[0] != '\\' || cursor[1] != 'u' ||
                        decode_hex_quad(cursor + 2, &low_surrogate) != MSYS_MIPC_OK ||
                        low_surrogate < 0xdc00u || low_surrogate > 0xdfffu) {
                        return MSYS_MIPC_INVALID_JSON;
                    }
                    cursor += 6;
                    codepoint = 0x10000u + ((codepoint - 0xd800u) << 10u) +
                        (low_surrogate - 0xdc00u);
                } else if (codepoint >= 0xdc00u && codepoint <= 0xdfffu) {
                    return MSYS_MIPC_INVALID_JSON;
                }
                byte_count = encode_utf8(codepoint, bytes);
                break;
            }
            default:
                return MSYS_MIPC_INVALID_JSON;
            }
        }
        if (written <= SIZE_MAX - byte_count) {
            size_t index;
            for (index = 0u; index < byte_count; ++index) {
                if (written + index + 1u < capacity) {
                    buffer[written + index] = (char)bytes[index];
                }
            }
            written += byte_count;
        } else {
            return MSYS_MIPC_TOO_LARGE;
        }
    }
    if (string_length != NULL) {
        *string_length = written;
    }
    if (written >= capacity) {
        buffer[capacity - 1u] = '\0';
        return MSYS_MIPC_BUFFER_TOO_SMALL;
    }
    buffer[written] = '\0';
    return MSYS_MIPC_OK;
}

int msys_mipc_json_get_u64(const char *json, const char *key, uint64_t *value)
{
    const char *cursor;
    const char *end;
    uint64_t parsed = 0;
    int result;

    if (value == NULL) {
        return MSYS_MIPC_INVALID_ARGUMENT;
    }
    *value = 0;
    result = find_top_level_value(json, key, &cursor, &end);
    if (result != MSYS_MIPC_OK) {
        return result;
    }
    if (cursor == end || *cursor == '-' || isdigit((unsigned char)*cursor) == 0) {
        return MSYS_MIPC_INVALID_JSON;
    }
    while (cursor < end && isdigit((unsigned char)*cursor) != 0) {
        unsigned digit = (unsigned)(*cursor - '0');
        if (parsed > (UINT64_MAX - digit) / 10u) {
            return MSYS_MIPC_INVALID_JSON;
        }
        parsed = parsed * 10u + digit;
        ++cursor;
    }
    if (cursor != end) {
        return MSYS_MIPC_INVALID_JSON;
    }
    *value = parsed;
    return MSYS_MIPC_OK;
}
