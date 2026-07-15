"""Zero-dependency mIPC transports for supervised MSYS components."""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable


MAX_PACKET = 256 * 1024


class MipcError(RuntimeError):
    code = "IPC_ERROR"


class MipcUnavailable(MipcError):
    code = "IPC_UNAVAILABLE"


class MipcRemoteError(MipcError):
    def __init__(
        self,
        code: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.code = code or "REMOTE_ERROR"
        self.message = message or self.code
        self.payload = payload or {}
        super().__init__(f"{self.code}: {self.message}")


def _encode(message: dict[str, Any], *, newline: bool) -> bytes:
    try:
        data = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MipcError(f"Cannot encode mIPC request: {exc}") from exc
    if len(data) > MAX_PACKET:
        raise MipcError("mIPC packet exceeds 256 KiB")
    return data + (b"\n" if newline else b"")


def _decode(data: bytes) -> dict[str, Any]:
    if not data:
        raise MipcError("Empty mIPC response")
    if len(data) > MAX_PACKET:
        raise MipcError("mIPC packet exceeds 256 KiB")
    try:
        message = json.loads(
            data.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MipcError(f"Invalid mIPC JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise MipcError("mIPC packet must be an object")
    return message


def _recv_line(sock: socket.socket, timeout: float) -> dict[str, Any]:
    sock.settimeout(timeout)
    data = bytearray()
    while True:
        remaining = MAX_PACKET + 1 - len(data)
        if remaining <= 0:
            raise MipcError("mIPC response exceeds 256 KiB")
        chunk = sock.recv(min(65536, remaining))
        if not chunk:
            break
        data.extend(chunk)
        newline = data.find(b"\n")
        if newline >= 0:
            del data[newline:]
            break
    return _decode(bytes(data))


Exchange = Callable[[Path, dict[str, Any], float], dict[str, Any]]
ComponentCallback = Callable[[dict[str, Any]], None]
ComponentCallHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _socket_exchange(
    socket_path: Path,
    request: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(socket_path))
            welcome = _recv_line(sock, timeout)
            if welcome.get("type") != "welcome":
                raise MipcError("Public control socket did not send welcome")
            sock.sendall(_encode(request, newline=True))
            return _recv_line(sock, timeout)
    except MipcError:
        raise
    except (OSError, TimeoutError) as exc:
        raise MipcUnavailable(f"Cannot connect to {socket_path}: {exc}") from exc


class PublicMipcClient:
    """One public request per Unix-stream connection."""

    def __init__(
        self,
        runtime_dir: str | os.PathLike[str] | None = None,
        *,
        default_timeout: float = 5.0,
        exchange: Exchange | None = None,
    ) -> None:
        selected = runtime_dir or os.environ.get("MSYS_RUNTIME_DIR", "/run/msys/main")
        self.socket_path = Path(selected) / "control.sock"
        self.default_timeout = float(default_timeout)
        self.exchange = exchange or _socket_exchange
        self._lock = threading.Lock()
        self._next_id = 1

    def call(
        self,
        target: str,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        if not target or not method:
            raise ValueError("mIPC target and method are required")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("mIPC payload must be an object")
        request_timeout = self.default_timeout if timeout is None else float(timeout)
        if request_timeout <= 0:
            raise ValueError("mIPC timeout must be positive")
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
        request = {
            "type": "call",
            "id": request_id,
            "target": target,
            "method": method,
            "payload": payload or {},
            "deadline_ms": int(time.monotonic() * 1000 + request_timeout * 1000),
            "idempotent": bool(idempotent),
        }
        response = self.exchange(self.socket_path, request, request_timeout)
        if not isinstance(response, dict):
            raise MipcError("mIPC response must be an object")
        if response.get("id") != request_id:
            raise MipcError("mIPC response id does not match request")
        if response.get("type") == "error":
            raw_payload = response.get("payload")
            raise MipcRemoteError(
                str(response.get("code") or "REMOTE_ERROR"),
                str(response.get("message") or "mIPC call failed"),
                raw_payload if isinstance(raw_payload, dict) else None,
            )
        if response.get("type") != "return":
            raise MipcError(f"Unexpected mIPC response: {response.get('type')!r}")
        raw = response.get("payload", {})
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        return {"value": raw}


class ComponentChannel:
    """Authenticated private component channel with lifecycle and RPC routing.

    One background pump owns socket reads. Replies are routed to per-call
    waiters while lifecycle and broadcast records retain their wire order in
    the UI callback. Ordinary apps therefore use their manifest ACL identity
    instead of the guest public control socket.
    """

    def __init__(self, sock: socket.socket, component: str, generation: int) -> None:
        self.sock = sock
        self.component = component
        self.generation = generation
        self.closed = threading.Event()
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any] | BaseException]] = {}
        self._pump_lock = threading.Lock()
        self._pump_thread: threading.Thread | None = None

    @classmethod
    def from_environment(cls) -> "ComponentChannel | None":
        raw_fd = os.environ.get("MSYS_CONTROL_FD")
        if not raw_fd:
            return None
        try:
            descriptor = int(raw_fd)
            generation = int(os.environ.get("MSYS_GENERATION", "0"))
        except ValueError as exc:
            raise MipcError("Invalid inherited mIPC metadata") from exc
        return cls(
            socket.socket(fileno=descriptor),
            os.environ.get("MSYS_COMPONENT_ID", "org.msys.apps:unknown"),
            generation,
        )

    def send(self, message: dict[str, Any]) -> None:
        data = _encode(message, newline=False)
        try:
            with self._send_lock:
                written = self.sock.send(data)
        except OSError as exc:
            raise MipcUnavailable(f"Component channel send failed: {exc}") from exc
        if written != len(data):
            raise MipcError("Short component-channel send")

    def call(
        self,
        target: str,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        if not target or not method:
            raise ValueError("mIPC target and method are required")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("mIPC payload must be an object")
        request_timeout = 5.0 if timeout is None else float(timeout)
        if request_timeout <= 0:
            raise ValueError("mIPC timeout must be positive")
        if self._pump_thread is None:
            raise MipcError("Component channel event pump is not running")
        waiter: queue.Queue[dict[str, Any] | BaseException] = queue.Queue(maxsize=1)
        with self._pending_lock:
            if self.closed.is_set():
                raise MipcUnavailable("Component channel is closed")
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = waiter
        try:
            self.send(
                {
                    "type": "call",
                    "id": request_id,
                    "target": target,
                    "method": method,
                    "payload": payload or {},
                    "deadline_ms": int(
                        time.monotonic() * 1000 + request_timeout * 1000
                    ),
                    "idempotent": bool(idempotent),
                }
            )
            try:
                response = waiter.get(timeout=request_timeout)
            except queue.Empty:
                raise MipcUnavailable(
                    f"mIPC call timed out: {target}.{method}"
                ) from None
            if isinstance(response, BaseException):
                raise response
            if response.get("type") == "error":
                raw_payload = response.get("payload")
                raise MipcRemoteError(
                    str(response.get("code") or "REMOTE_ERROR"),
                    str(response.get("message") or "mIPC call failed"),
                    raw_payload if isinstance(raw_payload, dict) else None,
                )
            if response.get("type") != "return":
                raise MipcError(f"Unexpected mIPC response: {response.get('type')!r}")
            raw = response.get("payload", {})
            if raw is None:
                return {}
            return raw if isinstance(raw, dict) else {"value": raw}
        finally:
            with self._pending_lock:
                if self._pending.get(request_id) is waiter:
                    del self._pending[request_id]

    def receive(self, timeout: float | None = None) -> dict[str, Any] | None:
        self.sock.settimeout(timeout)
        try:
            packet = self.sock.recv(MAX_PACKET + 1)
        except socket.timeout:
            return None
        except OSError as exc:
            if self.closed.is_set():
                return {"type": "eof"}
            raise MipcUnavailable(f"Component channel receive failed: {exc}") from exc
        if not packet:
            return {"type": "eof"}
        return _decode(packet)

    def handshake(self) -> None:
        self.send(
            {
                "type": "hello",
                "component": self.component,
                "generation": self.generation,
            }
        )
        welcome = self.receive(timeout=3.0)
        if welcome is None or welcome.get("type") != "welcome":
            raise MipcError("msysd did not accept component hello")
        self.send({"type": "ready"})

    def start(
        self,
        callback: ComponentCallback,
        *,
        call_handler: ComponentCallHandler | None = None,
    ) -> None:
        with self._pump_lock:
            if self._pump_thread is not None:
                raise MipcError("Component channel event pump is already running")
            thread = threading.Thread(
                target=self.pump,
                args=(callback, call_handler),
                name=f"msys-component-mipc:{self.component}",
                daemon=True,
            )
            self._pump_thread = thread
            thread.start()

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            try:
                waiter.put_nowait(error)
            except queue.Full:
                pass

    def pump(
        self,
        callback: ComponentCallback,
        call_handler: ComponentCallHandler | None = None,
    ) -> None:
        while not self.closed.is_set():
            try:
                message = self.receive(timeout=1.0)
            except MipcError as exc:
                self._fail_pending(exc)
                callback({"type": "eof"})
                return
            if message is None:
                continue
            request_id = message.get("id")
            if (
                message.get("type") in {"return", "error"}
                and isinstance(request_id, int)
                and not isinstance(request_id, bool)
            ):
                with self._pending_lock:
                    waiter = self._pending.get(request_id)
                if waiter is not None:
                    try:
                        waiter.put_nowait(message)
                    except queue.Full:
                        pass
                    continue
            if message.get("type") == "call" and call_handler is not None:
                if not isinstance(request_id, int) or isinstance(request_id, bool):
                    continue
                try:
                    payload = call_handler(message)
                    if not isinstance(payload, dict):
                        raise TypeError("component call handler must return an object")
                    self.send(
                        {"type": "return", "id": request_id, "payload": payload}
                    )
                except Exception as exc:
                    self.send(
                        {
                            "type": "error",
                            "id": request_id,
                            "code": "CALL_FAILED",
                            "message": str(exc)[:256],
                        }
                    )
                continue
            callback(message)
            if message.get("type") in {"eof", "shutdown"}:
                self._fail_pending(MipcUnavailable("Component channel closed"))
                return

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        self._fail_pending(MipcUnavailable("Component channel closed"))
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
