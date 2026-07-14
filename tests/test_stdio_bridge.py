from __future__ import annotations

import json
import os
import socket
import sys
import threading
import unittest

from msys_sdk.client import MAX_PACKET
from msys_sdk.stdio_bridge import BridgeProtocolError, StdioBridge, _validated_record


def recv_record(sock: socket.socket) -> dict:
    sock.settimeout(2)
    data = sock.recv(MAX_PACKET + 1)
    if not data:
        raise EOFError("bridge peer reached EOF")
    return json.loads(data.decode("utf-8"))


class StdioBridgeTests(unittest.TestCase):
    def make_bridge(self, source: str) -> tuple[StdioBridge, socket.socket]:
        peer, component = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(peer.close)
        self.addCleanup(component.close)
        environment = dict(os.environ)
        environment["MSYS_CONTROL_FD"] = str(component.fileno())
        bridge = StdioBridge(
            component,
            [sys.executable, "-c", source],
            child_env=environment,
        )
        return bridge, peer

    def test_bidirectional_json_line_translation_and_environment_boundary(self) -> None:
        child = r'''
import json, os, sys
assert "MSYS_CONTROL_FD" not in os.environ
assert os.environ["MSYS_MIPC_TRANSPORT"] == "stdio-jsonl-v1"
print(json.dumps({"type":"hello","component":"org.example:node","generation":1}), flush=True)
welcome = json.loads(sys.stdin.readline())
assert welcome["type"] == "welcome"
print(json.dumps({"type":"ready"}), flush=True)
event = json.loads(sys.stdin.readline())
print(json.dumps({"type":"event","topic":"child.echo","payload":event["payload"]}), flush=True)
'''
        bridge, peer = self.make_bridge(child)
        result: list[int] = []
        runner = threading.Thread(target=lambda: result.append(bridge.run()))
        runner.start()

        self.assertEqual(recv_record(peer)["type"], "hello")
        peer.sendall(b'{"type":"welcome","component":"org.example:node"}')
        self.assertEqual(recv_record(peer)["type"], "ready")
        peer.sendall(b'{"type":"event","payload":{"value":7}}')
        echoed = recv_record(peer)
        self.assertEqual(echoed["topic"], "child.echo")
        self.assertEqual(echoed["payload"], {"value": 7})

        runner.join(timeout=2)
        self.assertFalse(runner.is_alive())
        self.assertEqual(result, [0])

    def test_invalid_child_output_is_a_bridge_failure(self) -> None:
        bridge, _peer = self.make_bridge("print('ordinary stdout log', flush=True)")
        self.assertEqual(bridge.run(), 65)

    def test_child_cannot_close_output_and_keep_running(self) -> None:
        child = "import os, time; os.close(1); time.sleep(2)"
        bridge, _peer = self.make_bridge(child)
        self.assertEqual(bridge.run(), 65)

    def test_record_validation_is_bounded_and_object_only(self) -> None:
        self.assertEqual(_validated_record(b'{"type":"ready"}\n'), b'{"type":"ready"}')
        for invalid in (b"\n", b"[]\n", b"not-json\n", b"{}"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(BridgeProtocolError):
                    _validated_record(invalid)
        with self.assertRaises(BridgeProtocolError):
            _validated_record(b" " * (MAX_PACKET + 1) + b"\n")


if __name__ == "__main__":
    unittest.main()
