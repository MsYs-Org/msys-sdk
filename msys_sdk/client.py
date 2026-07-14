from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

MAX_PACKET = 256 * 1024


@dataclass(slots=True)
class MsysMessage:
    type: str
    payload: dict[str, Any]


class MsysClientError(RuntimeError):
    """Base error raised by the Python mIPC client."""


class MsysConnectionClosed(MsysClientError):
    """The component channel closed while an operation was pending."""


class MsysShutdown(MsysConnectionClosed):
    """msysd requested component shutdown."""


class MsysProtocolError(MsysClientError):
    """The component channel delivered an invalid protocol record."""


@dataclass(frozen=True, slots=True)
class _Failure:
    error_type: type[MsysClientError]
    arguments: tuple[object, ...]

    @classmethod
    def from_error(cls, error: MsysClientError) -> "_Failure":
        return cls(type(error), tuple(error.args))

    def make_error(self) -> MsysClientError:
        return self.error_type(*self.arguments)


_InboxItem = dict[str, Any] | _Failure
_ReplyQueue = queue.Queue[_InboxItem]


class MsysClient:
    def __init__(self, sock: socket.socket, component_id: str) -> None:
        self.sock = sock
        self.component_id = component_id
        self.generation = int(os.environ.get("MSYS_GENERATION", "0"))
        self._next_id = 1
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _ReplyQueue] = {}
        # All non-reply records retain their wire order here.  recv() and run()
        # are compatibility consumers of this event/control mailbox; neither
        # reads the descriptor itself.
        self._event_queue: queue.Queue[_InboxItem] = queue.Queue()
        self._reader_start_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        self._terminal_lock = threading.Lock()
        self._closed = threading.Event()
        self._terminal_call_error: MsysClientError | None = None
        self._terminal_recv_error: MsysClientError | None = None
        self._socket_close_lock = threading.Lock()
        self._socket_closed = False
        # A single blocking reader owns recv() from this point onward.  Queue
        # timeouts implement synchronous API deadlines without changing the
        # descriptor timeout underneath another thread.
        self.sock.setblocking(True)

    @classmethod
    def from_env(cls) -> "MsysClient":
        fd = int(os.environ["MSYS_CONTROL_FD"])
        sock = socket.socket(fileno=fd)
        return cls(sock, os.environ.get("MSYS_COMPONENT_ID", "unknown"))

    def send(self, message: dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_PACKET:
            raise ValueError("mIPC packet is too large")
        if self._closed.is_set():
            raise self._call_terminal_error()
        try:
            with self._send_lock:
                if self._closed.is_set():
                    raise self._call_terminal_error()
                self.sock.sendall(data)
        except MsysClientError:
            raise
        except OSError as exc:
            if self._closed.is_set():
                raise self._call_terminal_error() from exc
            error = MsysConnectionClosed(f"mIPC send failed: {exc}")
            self._terminate(error, recv_error=error)
            raise error from exc

    def recv(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Return the next unsolicited record without reading the fd directly.

        RPC replies for calls made through :meth:`call` are dispatched to that
        call's private waiter.  Welcome, event, inbound call, shutdown, and
        unknown reply records remain available through this compatibility API.
        Only one consumer should use ``recv()``/``run()`` for unsolicited
        records at a time.
        """

        if not self._closed.is_set():
            try:
                self._ensure_reader()
            except MsysClientError:
                if not self._closed.is_set():
                    raise
        if self._closed.is_set():
            try:
                item = self._event_queue.get_nowait()
            except queue.Empty:
                return self._terminal_recv_result()
        else:
            try:
                item = self._event_queue.get(timeout=timeout)
            except queue.Empty:
                if self._closed.is_set():
                    return self._terminal_recv_result()
                return None
        if isinstance(item, _Failure):
            raise item.make_error()
        return item

    def _ensure_reader(self) -> None:
        if self._closed.is_set():
            raise self._call_terminal_error()
        with self._reader_start_lock:
            if self._closed.is_set():
                raise self._call_terminal_error()
            if self._reader_thread is not None:
                return
            reader = threading.Thread(
                target=self._reader_loop,
                name=f"msys-mipc-reader:{self.component_id}",
                daemon=True,
            )
            self._reader_thread = reader
            reader.start()

    def _read_packet(self) -> dict[str, Any] | None:
        data = self.sock.recv(MAX_PACKET + 1)
        if not data:
            return None
        if len(data) > MAX_PACKET:
            raise MsysProtocolError("mIPC packet is too large")
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MsysProtocolError(f"invalid mIPC JSON record: {exc}") from exc
        if not isinstance(message, dict):
            raise MsysProtocolError("mIPC record must be a JSON object")
        return message

    def _reader_loop(self) -> None:
        try:
            while not self._closed.is_set():
                message = self._read_packet()
                if message is None:
                    self._terminate(
                        MsysConnectionClosed("mIPC component channel reached EOF"),
                        inbox_message={"type": "eof"},
                    )
                    return
                if message.get("type") == "shutdown":
                    reason = str(message.get("reason") or "msysd requested shutdown")
                    self._terminate(
                        MsysShutdown(reason),
                        inbox_message=message,
                    )
                    return
                self._dispatch_message(message)
        except MsysClientError as exc:
            self._terminate(exc, recv_error=exc)
        except OSError as exc:
            if not self._closed.is_set():
                error = MsysConnectionClosed(f"mIPC receive failed: {exc}")
                self._terminate(error, recv_error=error)
        except Exception as exc:  # keep pending callers from hanging on reader bugs
            error = MsysProtocolError(f"mIPC reader failed: {exc}")
            self._terminate(error, recv_error=error)
        finally:
            if self._closed.is_set():
                self._close_socket()

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        request_id = message.get("id")
        if (
            message_type in {"return", "error"}
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
                    else:
                        return
        self._event_queue.put(message)

    def _terminate(
        self,
        call_error: MsysClientError,
        *,
        inbox_message: dict[str, Any] | None = None,
        recv_error: MsysClientError | None = None,
    ) -> bool:
        with self._terminal_lock:
            if self._closed.is_set():
                return False
            self._terminal_call_error = call_error
            self._terminal_recv_error = recv_error
            self._closed.set()
        failure = _Failure.from_error(call_error)
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
            for waiter in waiters:
                try:
                    waiter.put_nowait(failure)
                except queue.Full:
                    pass
        if inbox_message is not None:
            self._event_queue.put(inbox_message)
        elif recv_error is not None:
            self._event_queue.put(_Failure.from_error(recv_error))
        return True

    def _call_terminal_error(self) -> MsysClientError:
        with self._terminal_lock:
            error = self._terminal_call_error or MsysConnectionClosed("mIPC client is closed")
            return type(error)(*error.args)

    def _terminal_recv_result(self) -> dict[str, Any]:
        with self._terminal_lock:
            error = self._terminal_recv_error
        if error is not None:
            raise type(error)(*error.args)
        return {"type": "eof"}

    def hello(self) -> dict[str, Any] | None:
        self.send({"type": "hello", "component": self.component_id, "generation": self.generation})
        return self.recv(timeout=2)

    def ready(self) -> None:
        self.send({"type": "ready"})

    def subscribe(self, topic: str) -> None:
        self.send({"type": "subscribe", "topic": topic})

    def event(self, topic: str, payload: dict[str, Any] | None = None) -> None:
        self.send({"type": "event", "topic": topic, "payload": payload or {}})

    def call(
        self,
        target: str,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        self._ensure_reader()
        waiter: _ReplyQueue = queue.Queue(maxsize=1)
        closed = False
        with self._pending_lock:
            if self._closed.is_set():
                closed = True
                request_id = -1
            else:
                request_id = self._next_id
                self._next_id += 1
                self._pending[request_id] = waiter
        if closed:
            raise self._call_terminal_error()
        try:
            self.send({
                "type": "call",
                "id": request_id,
                "target": target,
                "method": method,
                "payload": payload or {},
                "deadline_ms": int(time.monotonic() * 1000 + timeout * 1000),
                "idempotent": bool(idempotent),
            })
            try:
                item = waiter.get(timeout=max(0.0, timeout))
            except queue.Empty:
                # Serialize the final empty check with dispatcher delivery.  A
                # reply committed before the deadline is never misreported as
                # a timeout merely because the waiting thread woke late.
                with self._pending_lock:
                    try:
                        item = waiter.get_nowait()
                    except queue.Empty:
                        if self._pending.get(request_id) is waiter:
                            del self._pending[request_id]
                        raise TimeoutError(f"mIPC call timed out: {target}.{method}") from None
            if isinstance(item, _Failure):
                raise item.make_error()
            return item
        finally:
            with self._pending_lock:
                if self._pending.get(request_id) is waiter:
                    del self._pending[request_id]

    def call_role(
        self,
        role: str,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        return self.call(
            f"role:{role}",
            method,
            payload,
            timeout,
            idempotent=idempotent,
        )

    def call_interface(
        self,
        interface: str,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        return self.call(
            f"interface:{interface}",
            method,
            payload,
            timeout,
            idempotent=idempotent,
        )

    def call_component(
        self,
        component: str,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        return self.call(
            f"component:{component}",
            method,
            payload,
            timeout,
            idempotent=idempotent,
        )

    def discover(
        self,
        *,
        kind: str | None = None,
        name: str | None = None,
        timeout: float = 5,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if kind is not None:
            payload["kind"] = kind
        if name is not None:
            payload["name"] = name
        return self.call(
            "msys.core",
            "discover",
            payload,
            timeout,
            idempotent=True,
        )

    def broadcast(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
    ) -> dict[str, Any]:
        return self.call(
            "msys.core",
            "broadcast",
            {"topic": topic, "payload": payload or {}},
            timeout,
        )

    @staticmethod
    def public_call(
        target: str,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
        runtime_dir: str | None = None,
    ) -> dict[str, Any]:
        """Call msysd through the public per-session control socket.

        This is intended for applications that do not own a private component
        fd, or for explicit public-session tooling.  Component clients may call
        :meth:`call` concurrently with :meth:`run`; the private-channel reader
        dispatcher keeps events and RPC replies from competing on that fd.
        """
        runtime = Path(runtime_dir or os.environ.get("MSYS_RUNTIME_DIR", "/run/msys/main"))
        request = {
            "type": "call",
            "id": 1,
            "target": target,
            "method": method,
            "payload": payload or {},
            "deadline_ms": int(time.monotonic() * 1000 + timeout * 1000),
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(runtime / "control.sock"))
            _recv_line(sock, timeout)
            _send_line(sock, request)
            return _recv_line(sock, timeout)

    def run(self, on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("MsysClient.run() already has an event consumer")
        try:
            while True:
                msg = self.recv(timeout=None)
                if not msg or msg.get("type") in {"eof", "shutdown"}:
                    return
                if msg.get("type") == "event" and on_event:
                    on_event(msg)
        finally:
            self._run_lock.release()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        self._terminate(
            MsysConnectionClosed("mIPC client closed"),
            inbox_message={"type": "eof"},
        )
        self._close_socket()
        reader = self._reader_thread
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)

    def _close_socket(self) -> None:
        with self._socket_close_lock:
            if self._socket_closed:
                return
            self._socket_closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass

    def __enter__(self) -> "MsysClient":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def _send_line(sock: socket.socket, message: dict[str, Any]) -> None:
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_PACKET:
        raise ValueError("mIPC packet is too large")
    sock.sendall(data + b"\n")


def _recv_line(sock: socket.socket, timeout: float) -> dict[str, Any]:
    sock.settimeout(timeout)
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(MAX_PACKET + 1)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_PACKET:
            raise ValueError("mIPC packet is too large")
    if not data:
        raise EOFError("empty mIPC public response")
    return json.loads(data.decode("utf-8"))
