from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import BinaryIO, Sequence

from .client import MAX_PACKET


class BridgeProtocolError(RuntimeError):
    """The child did not speak the mIPC JSON-lines bridge protocol."""


@dataclass(frozen=True, slots=True)
class _PumpResult:
    source: str
    error: BaseException | None = None


def _validated_record(line: bytes) -> bytes:
    if not line.endswith(b"\n"):
        raise BridgeProtocolError("child mIPC record is not newline terminated")
    record = line[:-1]
    if record.endswith(b"\r"):
        record = record[:-1]
    if not record:
        raise BridgeProtocolError("child emitted an empty mIPC record")
    if len(record) > MAX_PACKET:
        raise BridgeProtocolError("child mIPC record is too large")
    try:
        value = json.loads(record.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeProtocolError(f"child emitted invalid mIPC JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BridgeProtocolError("child mIPC record must be a JSON object")
    return record


class StdioBridge:
    """Translate an inherited seqpacket component channel to child JSONL I/O.

    The wrapped application owns standard input and standard output exclusively
    for one-JSON-object-per-line mIPC records.  Its diagnostics remain ordinary
    text on standard error.  This keeps the child contract implementable with
    only a language's built-in JSON and stream APIs.
    """

    def __init__(
        self,
        control: socket.socket,
        command: Sequence[str],
        *,
        child_env: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("bridge child command is empty")
        self.control = control
        self.command = list(command)
        self.child_env = dict(os.environ if child_env is None else child_env)
        # The raw launch capability belongs to the bridge.  Leaving the number
        # in the child environment is both misleading and an accidental escape
        # from the language-neutral stream boundary.
        self.child_env.pop("MSYS_CONTROL_FD", None)
        self.child_env["MSYS_MIPC_TRANSPORT"] = "stdio-jsonl-v1"
        self._stop = threading.Event()
        self._results: queue.Queue[_PumpResult] = queue.Queue()
        self.process: subprocess.Popen[bytes] | None = None

    def _report(self, source: str, error: BaseException | None = None) -> None:
        self._results.put(_PumpResult(source, error))

    def _socket_to_child(self, stream: BinaryIO) -> None:
        try:
            while not self._stop.is_set():
                packet = self.control.recv(MAX_PACKET + 1)
                if not packet:
                    self._report("control-eof")
                    return
                if len(packet) > MAX_PACKET:
                    raise BridgeProtocolError("supervisor mIPC record is too large")
                try:
                    value = json.loads(packet.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise BridgeProtocolError(
                        f"supervisor emitted invalid mIPC JSON: {exc}"
                    ) from exc
                if not isinstance(value, dict):
                    raise BridgeProtocolError(
                        "supervisor mIPC record must be a JSON object"
                    )
                stream.write(packet + b"\n")
                stream.flush()
        except (BrokenPipeError, OSError) as exc:
            if not self._stop.is_set():
                self._report("control-input", exc)
        except BaseException as exc:
            self._report("control-input", exc)

    def _child_to_socket(self, stream: BinaryIO) -> None:
        try:
            while not self._stop.is_set():
                line = stream.readline(MAX_PACKET + 2)
                if not line:
                    self._report("child-eof")
                    return
                packet = _validated_record(line)
                self.control.sendall(packet)
        except (BrokenPipeError, OSError) as exc:
            if not self._stop.is_set():
                self._report("child-output", exc)
        except BaseException as exc:
            self._report("child-output", exc)

    def _terminate_child(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.5)
        except ProcessLookupError:
            pass

    def run(self) -> int:
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                env=self.child_env,
                close_fds=True,
            )
        except OSError as exc:
            print(f"msys-mipc-stdio: cannot start child: {exc}", file=sys.stderr)
            return 126

        assert self.process.stdin is not None
        assert self.process.stdout is not None
        input_thread = threading.Thread(
            target=self._socket_to_child,
            args=(self.process.stdin,),
            name="mipc-stdio-control-input",
            daemon=True,
        )
        output_thread = threading.Thread(
            target=self._child_to_socket,
            args=(self.process.stdout,),
            name="mipc-stdio-child-output",
            daemon=True,
        )
        input_thread.start()
        output_thread.start()

        failure: _PumpResult | None = None
        try:
            while self.process.poll() is None:
                try:
                    result = self._results.get(timeout=0.05)
                except queue.Empty:
                    continue
                if result.source == "child-eof":
                    # stdout commonly reaches EOF a few microseconds before
                    # waitpid observes a normal process exit.
                    try:
                        self.process.wait(timeout=0.15)
                    except subprocess.TimeoutExpired:
                        failure = _PumpResult(
                            "child-output",
                            BridgeProtocolError(
                                "child closed its mIPC output while still running"
                            ),
                        )
                    break
                failure = result
                break
            if failure is not None:
                self._terminate_child()
            returncode = self.process.wait()
        finally:
            self._stop.set()
            try:
                self.control.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.process.stdout.close()
            except OSError:
                pass
            input_thread.join(timeout=0.5)
            output_thread.join(timeout=0.5)

        if failure is not None:
            detail = f": {failure.error}" if failure.error is not None else ""
            print(
                f"msys-mipc-stdio: {failure.source} bridge failure{detail}",
                file=sys.stderr,
            )
            return 65
        return returncode if returncode >= 0 else 128 + abs(returncode)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="msys-mipc-stdio",
        description=(
            "bridge the inherited MSYS component socket to a child's JSON-lines "
            "standard input/output"
        ),
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="child command, conventionally introduced by --",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        _argument_parser().error("a child command is required after --")
    try:
        descriptor = int(os.environ["MSYS_CONTROL_FD"])
    except KeyError:
        print("msys-mipc-stdio: MSYS_CONTROL_FD is not set", file=sys.stderr)
        return 64
    except ValueError:
        print("msys-mipc-stdio: MSYS_CONTROL_FD is invalid", file=sys.stderr)
        return 64

    control = socket.socket(fileno=descriptor)
    bridge = StdioBridge(control, command)

    def forward(signum: int, _frame: object) -> None:
        process = bridge.process
        if process is not None and process.poll() is None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass

    previous: dict[int, object] = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, forward)
    try:
        return bridge.run()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        control.close()


if __name__ == "__main__":
    raise SystemExit(main())
