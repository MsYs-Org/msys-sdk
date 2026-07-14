from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from msys_sdk.client import (
    MsysClient,
    MsysConnectionClosed,
    MsysProtocolError,
    MsysShutdown,
)


def send_record(sock: socket.socket, message: dict) -> None:
    sock.sendall(json.dumps(message, separators=(",", ":")).encode("utf-8"))


def recv_record(sock: socket.socket, timeout: float = 1.0) -> dict:
    sock.settimeout(timeout)
    data = sock.recv(256 * 1024 + 1)
    if not data:
        raise EOFError("test peer reached EOF")
    return json.loads(data.decode("utf-8"))


class PythonClientTests(unittest.TestCase):
    def make_client(self) -> tuple[MsysClient, socket.socket]:
        peer, component = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        client = MsysClient(component, "org.example:test")
        self.addCleanup(peer.close)
        self.addCleanup(client.close)
        return client, peer

    def test_call_carries_deadline_and_idempotency(self) -> None:
        client, peer = self.make_client()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.call,
                "interface:org.example.echo.v1",
                "status",
                {"verbose": True},
                2,
                idempotent=True,
            )
            request = recv_record(peer)
            send_record(
                peer,
                {"type": "return", "id": request["id"], "payload": {"ok": True}},
            )
            response = future.result(timeout=1)

        self.assertEqual(response["payload"], {"ok": True})
        self.assertEqual(request["target"], "interface:org.example.echo.v1")
        self.assertEqual(request["method"], "status")
        self.assertTrue(request["idempotent"])
        self.assertGreater(request["deadline_ms"], 0)

    def test_hello_and_raw_recv_keep_synchronous_api(self) -> None:
        client, peer = self.make_client()
        with ThreadPoolExecutor(max_workers=1) as executor:
            hello = executor.submit(client.hello)
            request = recv_record(peer)
            self.assertEqual(request["type"], "hello")
            send_record(
                peer,
                {"type": "welcome", "component": "org.example:test", "generation": 1},
            )
            self.assertEqual(hello.result(timeout=1)["type"], "welcome")

        send_record(peer, {"type": "event", "topic": "raw.event", "payload": {}})
        self.assertEqual(client.recv(timeout=1)["topic"], "raw.event")
        self.assertIsNone(client.recv(timeout=0.01))

    def test_address_helpers_use_one_protocol(self) -> None:
        client, _peer = self.make_client()
        response = {"type": "return", "id": 1, "payload": {}}
        with mock.patch.object(client, "call", return_value=response) as call:
            self.assertIs(client.call_interface("org.example.echo.v1", "ping"), response)
            self.assertEqual(call.call_args.args[0], "interface:org.example.echo.v1")

            client.call_component("org.example.worker:sync", "wake")
            self.assertEqual(call.call_args.args[0], "component:org.example.worker:sync")

            client.call_role("launcher", "show")
            self.assertEqual(call.call_args.args[0], "role:launcher")

    def test_discovery_and_broadcast_are_core_calls(self) -> None:
        client, _peer = self.make_client()
        response = {"type": "return", "id": 1, "payload": {}}
        with mock.patch.object(client, "call", return_value=response) as call:
            client.discover(kind="capability", name="sensor.touch")
            self.assertEqual(call.call_args.args[:2], ("msys.core", "discover"))
            self.assertEqual(
                call.call_args.args[2],
                {"kind": "capability", "name": "sensor.touch"},
            )
            self.assertTrue(call.call_args.kwargs["idempotent"])

            client.broadcast("org.example.changed", {"value": 1})
            self.assertEqual(call.call_args.args[:2], ("msys.core", "broadcast"))
            self.assertEqual(call.call_args.args[2]["topic"], "org.example.changed")

    def test_concurrent_calls_and_event_reader_share_one_dispatcher(self) -> None:
        client, peer = self.make_client()
        events: list[dict] = []
        event_received = threading.Event()

        def on_event(message: dict) -> None:
            events.append(message)
            event_received.set()

        runner = threading.Thread(target=client.run, args=(on_event,))
        runner.start()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(client.call, "msys.core", "first", {}, 2)
            second = executor.submit(client.call, "msys.core", "second", {}, 2)
            requests = [recv_record(peer), recv_record(peer)]
            by_method = {request["method"]: request for request in requests}
            self.assertEqual(len({request["id"] for request in requests}), 2)

            send_record(
                peer,
                {"type": "event", "topic": "demo.tick", "payload": {"tick": 1}},
            )
            # Deliberately reply in reverse request order.  Each call must be
            # woken only by its own id while run() receives the event.
            for method in ("second", "first"):
                request = by_method[method]
                send_record(
                    peer,
                    {
                        "type": "return",
                        "id": request["id"],
                        "payload": {"method": method},
                    },
                )

            self.assertEqual(first.result(timeout=1)["payload"], {"method": "first"})
            self.assertEqual(second.result(timeout=1)["payload"], {"method": "second"})

        self.assertTrue(event_received.wait(1))
        self.assertEqual(events[0]["topic"], "demo.tick")
        send_record(peer, {"type": "shutdown", "reason": "test complete"})
        runner.join(timeout=1)
        self.assertFalse(runner.is_alive())

    def test_event_callback_can_make_reentrant_call(self) -> None:
        client, peer = self.make_client()
        callback_done = threading.Event()
        callback_reply: list[dict] = []

        def on_event(_message: dict) -> None:
            callback_reply.append(client.call("msys.core", "from-callback", timeout=1))
            callback_done.set()

        runner = threading.Thread(target=client.run, args=(on_event,))
        runner.start()
        send_record(peer, {"type": "event", "topic": "demo.callback", "payload": {}})
        request = recv_record(peer)
        send_record(
            peer,
            {"type": "return", "id": request["id"], "payload": {"ok": True}},
        )

        self.assertTrue(callback_done.wait(1))
        self.assertEqual(callback_reply[0]["payload"], {"ok": True})
        send_record(peer, {"type": "shutdown"})
        runner.join(timeout=1)
        self.assertFalse(runner.is_alive())

    def test_timed_out_reply_cannot_complete_a_later_call(self) -> None:
        client, peer = self.make_client()
        with ThreadPoolExecutor(max_workers=1) as executor:
            timed_out = executor.submit(client.call, "msys.core", "slow", {}, 0.05)
            first_request = recv_record(peer)
            with self.assertRaises(TimeoutError):
                timed_out.result(timeout=1)

            later = executor.submit(client.call, "msys.core", "later", {}, 1)
            second_request = recv_record(peer)
            send_record(
                peer,
                {"type": "return", "id": first_request["id"], "payload": {"late": True}},
            )
            send_record(
                peer,
                {"type": "return", "id": second_request["id"], "payload": {"later": True}},
            )
            self.assertEqual(later.result(timeout=1)["payload"], {"later": True})

        # Unknown/late replies remain visible to the legacy raw recv API.
        self.assertEqual(client.recv(timeout=0.2)["payload"], {"late": True})

    def test_shutdown_fails_pending_call_and_stops_future_operations(self) -> None:
        client, peer = self.make_client()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.call, "msys.core", "wait", {}, 5)
            recv_record(peer)
            send_record(peer, {"type": "shutdown", "reason": "session stopping"})
            with self.assertRaisesRegex(MsysShutdown, "session stopping"):
                pending.result(timeout=1)

        self.assertTrue(client.closed)
        self.assertEqual(client.recv(timeout=0.1)["type"], "shutdown")
        with self.assertRaises(MsysShutdown):
            client.call("msys.core", "after-shutdown")

    def test_close_unblocks_pending_call(self) -> None:
        client, peer = self.make_client()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.call, "msys.core", "wait", {}, 5)
            recv_record(peer)
            started = time.monotonic()
            client.close()
            with self.assertRaises(MsysConnectionClosed):
                pending.result(timeout=1)
        self.assertLess(time.monotonic() - started, 1)

    def test_protocol_error_wakes_recv_and_pending_calls(self) -> None:
        client, peer = self.make_client()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.call, "msys.core", "wait", {}, 5)
            recv_record(peer)
            peer.sendall(b"not-json")
            with self.assertRaises(MsysProtocolError):
                pending.result(timeout=1)
        with self.assertRaises(MsysProtocolError):
            client.recv(timeout=0.1)


if __name__ == "__main__":
    unittest.main()
