#ifndef MSYS_MIPC_H
#define MSYS_MIPC_H

/*
 * Minimal C SDK for the current MSYS JSON mIPC protocol.
 *
 * One UTF-8 JSON object is carried in each AF_UNIX/SOCK_SEQPACKET record.
 * This is the v0 userspace wire format used by the Python msysd prototype;
 * it is intentionally distinct from the reserved native binary wire format.
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MSYS_MIPC_MAX_PACKET (256u * 1024u)
#define MSYS_MIPC_RECV_CAPACITY (MSYS_MIPC_MAX_PACKET + 1u)

enum msys_mipc_result {
    MSYS_MIPC_OK = 0,
    MSYS_MIPC_TIMEOUT = 1,
    MSYS_MIPC_EOF = 2,
    MSYS_MIPC_NOT_FOUND = 3,

    MSYS_MIPC_INVALID_ARGUMENT = -1,
    MSYS_MIPC_INVALID_FD = -2,
    MSYS_MIPC_IO_ERROR = -3,
    MSYS_MIPC_TOO_LARGE = -4,
    MSYS_MIPC_BUFFER_TOO_SMALL = -5,
    MSYS_MIPC_INVALID_JSON = -6
};

typedef struct msys_mipc_client {
    int fd;
    int owns_fd;
    int last_errno;
} msys_mipc_client;

/*
 * Bind a client to an existing AF_UNIX/SOCK_SEQPACKET descriptor.
 * If take_ownership is non-zero, msys_mipc_client_close() closes fd.
 */
int msys_mipc_client_init(msys_mipc_client *client, int fd, int take_ownership);

/*
 * Read and validate MSYS_CONTROL_FD. The inherited descriptor remains owned
 * by the process, so client_close() only detaches the client from it.
 */
int msys_mipc_client_from_env(msys_mipc_client *client);

void msys_mipc_client_close(msys_mipc_client *client);
int msys_mipc_client_fd(const msys_mipc_client *client);
int msys_mipc_last_errno(const msys_mipc_client *client);
const char *msys_mipc_result_string(int result);

/* Send one complete JSON object as one SOCK_SEQPACKET record. */
int msys_mipc_send_json(msys_mipc_client *client, const char *json_object);

int msys_mipc_send_hello(
    msys_mipc_client *client,
    const char *component_id,
    uint64_t generation
);

/* Uses MSYS_COMPONENT_ID (default "unknown") and MSYS_GENERATION (default 0). */
int msys_mipc_send_hello_from_env(msys_mipc_client *client);

int msys_mipc_send_ready(msys_mipc_client *client);
int msys_mipc_send_subscribe(msys_mipc_client *client, const char *topic);

/* payload_json is inserted as a JSON value and defaults to {} when NULL. */
int msys_mipc_send_event_json(
    msys_mipc_client *client,
    const char *topic,
    const char *payload_json
);

/* Read CLOCK_MONOTONIC in milliseconds for constructing an RPC deadline. */
int msys_mipc_monotonic_ms(uint64_t *value);

/*
 * Send one language-neutral RPC request. target accepts role:<name>,
 * interface:<name>, component:<package>:<component>, or msys.core.
 * deadline_ms is an absolute CLOCK_MONOTONIC deadline. Setting idempotent lets
 * msysd retry a liveness failure against a fallback provider.
 */
int msys_mipc_send_call_json(
    msys_mipc_client *client,
    uint64_t request_id,
    const char *target,
    const char *method,
    const char *payload_json,
    uint64_t deadline_ms,
    int idempotent
);

int msys_mipc_send_return_json(
    msys_mipc_client *client,
    uint64_t request_id,
    const char *payload_json
);

/* Optional convenience for providers that need to reject an incoming call. */
int msys_mipc_send_error(
    msys_mipc_client *client,
    uint64_t request_id,
    const char *code,
    const char *message
);

/*
 * Receive exactly one packet and NUL-terminate it.
 *
 * timeout_ms: -1 waits forever, 0 polls, positive values wait that many ms.
 * json_len receives the packet byte length (without the NUL). If capacity is
 * too small, the packet is left queued and json_len reports the required byte
 * length; pass a buffer of json_len + 1 bytes and call again.
 */
int msys_mipc_recv_json(
    msys_mipc_client *client,
    char *buffer,
    size_t capacity,
    int timeout_ms,
    size_t *json_len
);

/*
 * Lightweight helpers for top-level protocol fields. They are not a general
 * JSON DOM. raw_value points into json and remains valid while json does.
 */
int msys_mipc_json_get_raw(
    const char *json,
    const char *key,
    const char **raw_value,
    size_t *raw_length
);

int msys_mipc_json_get_string(
    const char *json,
    const char *key,
    char *buffer,
    size_t capacity,
    size_t *string_length
);

int msys_mipc_json_get_u64(
    const char *json,
    const char *key,
    uint64_t *value
);

#ifdef __cplusplus
}
#endif

#endif
