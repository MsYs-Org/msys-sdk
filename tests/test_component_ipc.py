from __future__ import annotations

import json
import socket
import threading
import unittest
from pathlib import Path

from msys_sdk.component_ipc import (
    ComponentChannel,
    MipcError,
    MipcRemoteError,
    PublicMipcClient,
)
from msys_sdk.application_navigation import application_navigation_handler


class PublicMipcClientTests(unittest.TestCase):
    def test_call_builds_bounded_idempotent_request(self) -> None:
        seen: list[tuple[Path, dict, float]] = []

        def exchange(path, request, timeout):
            seen.append((path, request, timeout))
            return {"type": "return", "id": request["id"], "payload": {"ok": True}}

        result = PublicMipcClient("/tmp/example", exchange=exchange).call(
            "msys.core", "list_roles", {}, timeout=2.0, idempotent=True
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen[0][0], Path("/tmp/example/control.sock"))
        self.assertTrue(seen[0][1]["idempotent"])
        self.assertGreater(seen[0][1]["deadline_ms"], 0)

    def test_remote_error_preserves_safe_details(self) -> None:
        def exchange(_path, request, _timeout):
            return {
                "type": "error",
                "id": request["id"],
                "code": "NO_PROVIDER",
                "message": "missing",
                "payload": {"role": "hal-manager"},
            }

        with self.assertRaises(MipcRemoteError) as caught:
            PublicMipcClient(exchange=exchange).call("role:hal-manager", "status")
        self.assertEqual(caught.exception.code, "NO_PROVIDER")
        self.assertEqual(caught.exception.payload["role"], "hal-manager")

    def test_response_id_mismatch_is_rejected(self) -> None:
        def exchange(_path, _request, _timeout):
            return {"type": "return", "id": 999, "payload": {}}

        with self.assertRaisesRegex(MipcError, "id does not match"):
            PublicMipcClient(exchange=exchange).call("msys.core", "list_roles")

    def test_non_object_request_and_response_are_rejected(self) -> None:
        client = PublicMipcClient(exchange=lambda *_args: [])
        with self.assertRaisesRegex(ValueError, "payload must be an object"):
            client.call("msys.core", "method", [1])  # type: ignore[arg-type]
        with self.assertRaisesRegex(MipcError, "response must be an object"):
            client.call("msys.core", "method")


@unittest.skipUnless(hasattr(socket, "SOCK_SEQPACKET"), "SOCK_SEQPACKET unavailable")
class ComponentChannelTests(unittest.TestCase):
    def test_hello_welcome_ready_handshake(self) -> None:
        app_socket, daemon_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        received: list[dict] = []

        def daemon() -> None:
            import json

            hello = json.loads(daemon_socket.recv(65536).decode("utf-8"))
            received.append(hello)
            daemon_socket.send(b'{"type":"welcome"}')
            ready = json.loads(daemon_socket.recv(65536).decode("utf-8"))
            received.append(ready)

        thread = threading.Thread(target=daemon)
        thread.start()
        channel = ComponentChannel(app_socket, "org.msys.apps:notes", 3)
        channel.handshake()
        thread.join(timeout=2.0)
        channel.close()
        daemon_socket.close()
        self.assertEqual(received[0]["type"], "hello")
        self.assertEqual(received[0]["generation"], 3)
        self.assertEqual(received[1], {"type": "ready"})

    def test_private_rpc_and_event_pump_share_one_reader(self) -> None:
        app_socket, daemon_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        channel = ComponentChannel(app_socket, "org.msys.apps:device-info", 4)
        events: list[dict] = []
        channel.start(events.append)

        result: list[dict] = []
        errors: list[BaseException] = []

        def caller() -> None:
            try:
                result.append(
                    channel.call(
                        "msys.core",
                        "list_components",
                        {},
                        timeout=1.0,
                        idempotent=True,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=caller)
        thread.start()
        request = json.loads(daemon_socket.recv(65536).decode("utf-8"))
        daemon_socket.send(
            json.dumps(
                {"type": "event", "topic": "msys.test", "payload": {"n": 1}}
            ).encode("utf-8")
        )
        daemon_socket.send(
            json.dumps(
                {
                    "type": "return",
                    "id": request["id"],
                    "payload": {"components": []},
                }
            ).encode("utf-8")
        )
        thread.join(timeout=2)
        channel.close()
        daemon_socket.close()

        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result, [{"components": []}])
        self.assertEqual(events[0]["topic"], "msys.test")
        self.assertTrue(request["idempotent"])

    def test_private_rpc_error_is_typed(self) -> None:
        app_socket, daemon_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        channel = ComponentChannel(app_socket, "org.msys.apps:device-info", 1)
        channel.start(lambda _message: None)

        def daemon() -> None:
            request = json.loads(daemon_socket.recv(65536).decode("utf-8"))
            daemon_socket.send(
                json.dumps(
                    {
                        "type": "error",
                        "id": request["id"],
                        "code": "ACCESS_DENIED",
                        "message": "manifest permission missing",
                        "payload": {"permission": "mipc.call:msys.core"},
                    }
                ).encode("utf-8")
            )

        thread = threading.Thread(target=daemon)
        thread.start()
        with self.assertRaises(MipcRemoteError) as caught:
            channel.call("msys.core", "list_components", timeout=1)
        channel.close()
        daemon_socket.close()
        thread.join(timeout=1)
        self.assertEqual(caught.exception.code, "ACCESS_DENIED")
        self.assertEqual(caught.exception.payload["permission"], "mipc.call:msys.core")

    def test_inbound_application_navigation_call_is_replied_by_handler(self) -> None:
        app_socket, daemon_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        channel = ComponentChannel(app_socket, "org.example.app:main", 2)
        unhandled: list[dict] = []
        back_calls: list[str] = []
        channel.start(
            unhandled.append,
            call_handler=application_navigation_handler(
                lambda: back_calls.append("back") is None
            ),
        )

        daemon_socket.send(
            json.dumps(
                {"type": "call", "id": 41, "method": "navigation_back", "payload": {}}
            ).encode("utf-8")
        )
        reply = json.loads(daemon_socket.recv(65536).decode("utf-8"))
        channel.close()
        daemon_socket.close()

        self.assertEqual(reply, {"type": "return", "id": 41, "payload": {"handled": True}})
        self.assertEqual(back_calls, ["back"])
        self.assertEqual(unhandled, [])

    def test_application_navigation_declines_unknown_method(self) -> None:
        handler = application_navigation_handler(lambda: True)
        self.assertEqual(
            handler({"type": "call", "method": "not_navigation"}),
            {"handled": False, "reason": "method-not-supported"},
        )


if __name__ == "__main__":
    unittest.main()
