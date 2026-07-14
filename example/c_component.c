#include "msys/mipc.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int report_error(const msys_mipc_client *client, const char *operation, int result)
{
    int saved_errno = msys_mipc_last_errno(client);
    fprintf(stderr, "%s: %s", operation, msys_mipc_result_string(result));
    if (saved_errno != 0) {
        fprintf(stderr, ": %s", strerror(saved_errno));
    }
    fputc('\n', stderr);
    return 1;
}

int main(void)
{
    msys_mipc_client client;
    char *packet;
    int result;

    result = msys_mipc_client_from_env(&client);
    if (result != MSYS_MIPC_OK) {
        return report_error(&client, "open MSYS_CONTROL_FD", result);
    }
    packet = (char *)malloc(MSYS_MIPC_RECV_CAPACITY);
    if (packet == NULL) {
        perror("allocate receive buffer");
        return 1;
    }

    result = msys_mipc_send_hello_from_env(&client);
    if (result != MSYS_MIPC_OK) {
        free(packet);
        return report_error(&client, "send hello", result);
    }
    result = msys_mipc_recv_json(&client, packet, MSYS_MIPC_RECV_CAPACITY, 2000, NULL);
    if (result != MSYS_MIPC_OK) {
        free(packet);
        return report_error(&client, "receive welcome", result);
    }

    result = msys_mipc_send_subscribe(&client, "org.msys.demo.tick");
    if (result == MSYS_MIPC_OK) {
        result = msys_mipc_send_ready(&client);
    }
    if (result == MSYS_MIPC_OK) {
        result = msys_mipc_send_event_json(
            &client,
            "org.msys.demo.native.started",
            "{\"language\":\"c\",\"ready\":true}"
        );
    }
    if (result != MSYS_MIPC_OK) {
        free(packet);
        return report_error(&client, "announce component", result);
    }

    for (;;) {
        char type[32];
        size_t type_length = 0;

        result = msys_mipc_recv_json(&client, packet, MSYS_MIPC_RECV_CAPACITY, -1, NULL);
        if (result == MSYS_MIPC_EOF) {
            break;
        }
        if (result != MSYS_MIPC_OK) {
            free(packet);
            return report_error(&client, "receive packet", result);
        }
        result = msys_mipc_json_get_string(packet, "type", type, sizeof(type), &type_length);
        if (result != MSYS_MIPC_OK) {
            fprintf(stderr, "ignored packet without a usable type\n");
            continue;
        }
        if (strcmp(type, "shutdown") == 0) {
            break;
        }
        if (strcmp(type, "event") == 0) {
            const char *payload;
            size_t payload_length;
            if (msys_mipc_json_get_raw(packet, "payload", &payload, &payload_length) == MSYS_MIPC_OK) {
                printf("event payload: %.*s\n", (int)payload_length, payload);
                fflush(stdout);
            }
            continue;
        }
        if (strcmp(type, "call") == 0) {
            uint64_t request_id;
            char method[64];
            if (msys_mipc_json_get_u64(packet, "id", &request_id) != MSYS_MIPC_OK ||
                msys_mipc_json_get_string(packet, "method", method, sizeof(method), NULL) != MSYS_MIPC_OK) {
                continue;
            }
            if (strcmp(method, "ping") == 0) {
                result = msys_mipc_send_return_json(
                    &client,
                    request_id,
                    "{\"ok\":true,\"runtime\":\"native-c\"}"
                );
            } else {
                result = msys_mipc_send_error(&client, request_id, "NO_METHOD", method);
            }
            if (result != MSYS_MIPC_OK) {
                free(packet);
                return report_error(&client, "reply to call", result);
            }
        }
    }

    free(packet);
    msys_mipc_client_close(&client);
    return 0;
}
