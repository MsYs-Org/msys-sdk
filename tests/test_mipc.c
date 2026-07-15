#define _POSIX_C_SOURCE 200809L

#include "msys/mipc.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        return 1; \
    } \
} while (0)

static int receive_exact(int fd, const char *expected)
{
    char packet[1024];
    ssize_t received = recv(fd, packet, sizeof(packet) - 1u, 0);
    CHECK(received >= 0);
    packet[(size_t)received] = '\0';
    CHECK(strcmp(packet, expected) == 0);
    return 0;
}

int main(void)
{
    int sockets[2];
    msys_mipc_client client;
    char packet[512];
    char text[64];
    const char *raw;
    size_t length;
    uint64_t id;
    uint64_t now_ms;

    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sockets) == 0);
    CHECK(msys_mipc_client_init(&client, sockets[1], 0) == MSYS_MIPC_OK);

    CHECK(msys_mipc_send_hello(&client, "org.msys.test:native", 7u) == MSYS_MIPC_OK);
    CHECK(receive_exact(
        sockets[0],
        "{\"type\":\"hello\",\"component\":\"org.msys.test:native\",\"generation\":7}"
    ) == 0);
    CHECK(msys_mipc_send_ready(&client) == MSYS_MIPC_OK);
    CHECK(receive_exact(sockets[0], "{\"type\":\"ready\"}") == 0);
    CHECK(msys_mipc_send_subscribe(&client, "topic.\"quoted\"") == MSYS_MIPC_OK);
    CHECK(receive_exact(
        sockets[0],
        "{\"type\":\"subscribe\",\"topic\":\"topic.\\\"quoted\\\"\"}"
    ) == 0);
    CHECK(msys_mipc_send_event_json(&client, "demo.tick", "{\"tick\":1}") == MSYS_MIPC_OK);
    CHECK(receive_exact(
        sockets[0],
        "{\"type\":\"event\",\"topic\":\"demo.tick\",\"payload\":{\"tick\":1}}"
    ) == 0);
    CHECK(msys_mipc_monotonic_ms(&now_ms) == MSYS_MIPC_OK);
    CHECK(now_ms > 0u);
    CHECK(msys_mipc_send_call_json(
        &client,
        17u,
        "interface:org.example.echo.v1",
        "echo\"value",
        "{\"text\":\"hello\"}",
        123456u,
        1
    ) == MSYS_MIPC_OK);
    CHECK(receive_exact(
        sockets[0],
        "{\"type\":\"call\",\"id\":17,\"target\":\"interface:org.example.echo.v1\","
        "\"method\":\"echo\\\"value\",\"payload\":{\"text\":\"hello\"},"
        "\"deadline_ms\":123456,\"idempotent\":true}"
    ) == 0);
    CHECK(msys_mipc_send_call_json(
        &client, 18u, "", "echo", NULL, 1u, 0
    ) == MSYS_MIPC_INVALID_ARGUMENT);
    CHECK(msys_mipc_send_return_json(&client, 42u, "{\"ok\":true}") == MSYS_MIPC_OK);
    CHECK(receive_exact(
        sockets[0],
        "{\"type\":\"return\",\"id\":42,\"payload\":{\"ok\":true}}"
    ) == 0);
    CHECK(strcmp(
        MSYS_APPLICATION_NAVIGATION_INTERFACE,
        "org.msys.application-navigation.v1"
    ) == 0);
    CHECK(strcmp(MSYS_NAVIGATION_BACK_METHOD, "navigation_back") == 0);
    CHECK(msys_mipc_send_navigation_back_result(&client, 43u, 1) == MSYS_MIPC_OK);
    CHECK(receive_exact(
        sockets[0],
        "{\"type\":\"return\",\"id\":43,\"payload\":{\"handled\":true}}"
    ) == 0);
    CHECK(msys_mipc_send_navigation_back_result(&client, 44u, 0) == MSYS_MIPC_OK);
    CHECK(receive_exact(
        sockets[0],
        "{\"type\":\"return\",\"id\":44,\"payload\":{\"handled\":false}}"
    ) == 0);

    CHECK(send(
        sockets[0],
        "{\"type\":\"call\",\"id\":99,\"method\":\"ping\",\"payload\":{\"nested\":[1,true]}}",
        strlen("{\"type\":\"call\",\"id\":99,\"method\":\"ping\",\"payload\":{\"nested\":[1,true]}}"),
        0
    ) > 0);
    CHECK(msys_mipc_recv_json(&client, packet, 8u, 0, &length) == MSYS_MIPC_BUFFER_TOO_SMALL);
    CHECK(length > 8u);
    CHECK(msys_mipc_recv_json(&client, packet, sizeof(packet), 0, &length) == MSYS_MIPC_OK);
    CHECK(msys_mipc_json_get_string(packet, "type", text, sizeof(text), NULL) == MSYS_MIPC_OK);
    CHECK(strcmp(text, "call") == 0);
    CHECK(msys_mipc_json_get_u64(packet, "id", &id) == MSYS_MIPC_OK);
    CHECK(id == 99u);
    CHECK(msys_mipc_json_get_string(packet, "method", text, sizeof(text), NULL) == MSYS_MIPC_OK);
    CHECK(strcmp(text, "ping") == 0);
    CHECK(msys_mipc_json_get_raw(packet, "payload", &raw, &length) == MSYS_MIPC_OK);
    CHECK(length == strlen("{\"nested\":[1,true]}"));
    CHECK(strncmp(raw, "{\"nested\":[1,true]}", length) == 0);
    CHECK(msys_mipc_json_get_raw(packet, "missing", &raw, &length) == MSYS_MIPC_NOT_FOUND);
    CHECK(msys_mipc_json_get_raw("{\"id\":truex}", "id", &raw, &length) == MSYS_MIPC_INVALID_JSON);

    CHECK(msys_mipc_json_get_string(
        "{\"text\":\"line\\n\\u4e2d\\ud83d\\ude00\"}",
        "text",
        text,
        sizeof(text),
        &length
    ) == MSYS_MIPC_OK);
    CHECK(strcmp(text, "line\n\xe4\xb8\xad\xf0\x9f\x98\x80") == 0);
    CHECK(length == strlen(text));

    CHECK(msys_mipc_recv_json(&client, packet, sizeof(packet), 0, NULL) == MSYS_MIPC_TIMEOUT);
    CHECK(msys_mipc_send_json(&client, "[]") == MSYS_MIPC_INVALID_JSON);

    msys_mipc_client_close(&client);
    close(sockets[0]);
    close(sockets[1]);
    puts("mIPC C SDK tests passed");
    return 0;
}
