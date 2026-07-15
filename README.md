# msys-sdk

Language SDKs for MSYS components and applications. They communicate directly
with `msysd`; there is no dependency on systemd, D-Bus, a package manager, or a
third-party IPC library.

## C / C++ SDK

The C SDK implements the protocol currently used by MSYS components:

- `msysd` creates a private `AF_UNIX` `SOCK_SEQPACKET` socketpair;
- the child receives its endpoint as the decimal `MSYS_CONTROL_FD` value;
- every socket record contains one UTF-8 JSON object, up to 256 KiB;
- the child sends `hello`, receives `welcome`, subscribes as needed, then sends
  `ready`;
- `call`, `event`, and `shutdown` messages can arrive on the same descriptor.

This JSON record protocol is the prototype mIPC v0 wire format. The binary
header under `msys-core/native` is reserved for a later negotiated native wire
format and must not be written to the current `MSYS_CONTROL_FD`.

Build and test on Linux:

```sh
make
make check
```

This creates:

- `build/libmsys-mipc.a` — static, dependency-free C library;
- `build/msys-c-component` — runnable provider/component example;
- `build/test-mipc` — socketpair protocol and JSON-helper test.

The public header is C++ safe:

```cpp
#include <msys/mipc.h>
```

To install into a staging root without invoking a target package manager:

```sh
make DESTDIR=/opt/msys-sdk-stage PREFIX=/usr install
```

### Component skeleton

```c
#include <msys/mipc.h>
#include <string.h>

int main(void) {
    msys_mipc_client client;
    char packet[MSYS_MIPC_RECV_CAPACITY];

    if (msys_mipc_client_from_env(&client) != MSYS_MIPC_OK)
        return 1;
    if (msys_mipc_send_hello_from_env(&client) != MSYS_MIPC_OK)
        return 1;
    if (msys_mipc_recv_json(&client, packet, sizeof(packet), 2000, 0)
        != MSYS_MIPC_OK)
        return 1; /* welcome */
    if (msys_mipc_send_ready(&client) != MSYS_MIPC_OK)
        return 1;

    for (;;) {
        char type[32];

        if (msys_mipc_recv_json(&client, packet, sizeof(packet), -1, 0)
            != MSYS_MIPC_OK)
            break;
        if (msys_mipc_json_get_string(packet, "type", type, sizeof(type), 0)
            != MSYS_MIPC_OK)
            continue;
        if (strcmp(type, "shutdown") == 0)
            break;
        /* See example/c_component.c for event and call handling. */
    }
    return 0;
}
```

Payload arguments such as `payload_json` are raw JSON values. The SDK escapes
all string arguments, but deliberately does not include a full JSON encoder.
Use the lightweight top-level accessors for protocol fields (`type`, `id`,
`method`, `payload`) or pair `msys_mipc_recv_json()` with the application's JSON
library when it already has one. Only one thread should read a component socket.
The C helper deliberately does not start a reader thread or allocate request
waiters: applications using it must nominate exactly one receive loop and
dispatch replies/events themselves. Concurrent calls must never invoke
`msys_mipc_recv_json()` from multiple threads. This is a documented native API
boundary, not an implicit locking guarantee.

## Python SDK

The Python client speaks the same JSON mIPC protocol:

```python
from msys_sdk import MsysClient

client = MsysClient.from_env()
client.hello()
client.subscribe("msys.install.package_changed")
client.ready()
client.event("app.started", {"ok": True})
```

### Shortest application declaration path

Generate only the language-neutral `manifest.json` (this command does not copy
a scaffold, download a runtime, or invoke a package manager):

```sh
msys-app-manifest --id org.example.hello --runtime tk --name "Hello" \
  --output manifest.json
```

Put the entry point at the generated `exec` path, then use the workstation
tool's existing validate/build/deliver path from the application directory:

```sh
msys-dev package validate manifest.json
msys-dev app run .
```

Use `--headless` for a component with no X11 surface. Add `--interface`,
`--capability`, or `--role` only when the process really implements that mIPC
provider contract; provider declarations default to on-demand,
`mipc-ready`, and hidden from the launcher. Override the exact argv with
`--entrypoint` and repeated `--arg` options. The same declaration format
supports `python`, `tk`, `c`, `cpp`, `qt`, and `electron`.

For small supervised Tk applications, `ComponentChannel` owns the private
component socket and readiness handshake, `TouchApplication` supplies the
shared responsive window/input lifecycle, and `PackageI18n` loads an
application-owned catalog with recovery strings:

```python
from msys_sdk import ComponentChannel, PackageI18n, TouchApplication
```

These are framework contracts, not an application registry. Every application
keeps its own package id, manifest, catalog, state and release lifecycle.

The Python implementation owns exactly one background reader per component
descriptor. `recv()` and `run()` consume an in-memory unsolicited-message
queue; they never call socket `recv()` themselves. Reply records are routed by
request id to a private waiter, so synchronous calls may safely overlap the
event loop and may run concurrently from multiple application threads:

```python
import threading

events = threading.Thread(target=client.run, args=(handle_event,), daemon=True)
events.start()

# Safe while run() is waiting for broadcasts; a callback may also call this.
reply = client.call_interface("org.example.echo.v1", "ping", timeout=3)
```

Use only one `recv()`/`run()` consumer for unsolicited messages. Multiple
concurrent `call()` operations are supported and replies may arrive in any
order. A timed-out request is unregistered atomically, so its late reply cannot
complete a newer request. EOF, malformed input, local `close()`, and an incoming
`shutdown` wake every pending caller immediately. `MsysConnectionClosed`,
`MsysShutdown`, and `MsysProtocolError` are exported for explicit handling;
call timeouts continue to raise the built-in `TimeoutError`.

Run the Python dispatcher tests without installing the SDK:

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py' -v
```

### Lightweight i18n

The Python SDK also implements the static `msys.i18n.catalog.v1` contract. One
UTF-8 JSON file contains a complete default locale plus partial locale
overlays, so the same resource can be read directly by Qt/C++, Electron/Node,
C, Tk, or Python without a translation daemon or third-party dependency:

```python
from msys_sdk import Translator

i18n = Translator.from_file(
    "files/share/i18n/catalog.json",
    # Omit locale to use MSYS_LOCALE, LC_ALL, LC_MESSAGES, then LANG.
    locale="zh-CN",
)

window_title = i18n.text("window.title")
status = i18n.text("wifi.connected", {"ssid": "Lab"})
file_count = i18n.plural("files", 3)
```

Missing translations return the caller's fallback or the key, append a bounded
diagnostic, and do not crash the application. The only message syntax is named
`{placeholder}` replacement with `{{` and `}}` escapes; it does not execute
Python formatting expressions. See [`docs/i18n.md`](docs/i18n.md) for package
layout, validation, locale switching, and direct Qt/Electron/C consumption.
The same guide documents optional `x-msys-i18n` manifest keys for localized
launcher names and summaries; they remain presentation metadata only.

Native applications can compile that same validated JSON into a static header
with `python3 -m msys_sdk.i18n_c catalog.json app_catalog.h --symbol
app_catalog`, then use `<msys/i18n.h>`. Lookup, integer plural selection, and
safe named interpolation are allocation-free and require neither a target JSON
library nor ICU.

### Shared Tk and Qt font policy

Graphical system components use one dependency-free policy without importing a
toolkit until the application asks for it:

```python
from msys_sdk import configure_tk_fonts, font_spec

root = tkinter.Tk()
configure_tk_fonts(root, default_size=10)
label = tkinter.Label(root, text="设置", font=font_spec(root, 11, "bold"))
```

For supervised Tk roots, `configure_tk_fonts` also repairs Tk's capitalized
`WM_CLASS` and publishes `_MSYS_APP_ID`, `_MSYS_COMPONENT_ID`, and
`_MSYS_WINDOW_ROLE`. Independently managed `Toplevel` windows can opt in
directly:

```python
from msys_sdk import configure_tk_window_identity

configure_tk_window_identity(window, "org.example.app")
```

This is a best-effort no-op outside X11 and has no third-party dependency.

`configure_qt_fonts(app, QtGui)` applies the same installed-family preference,
pixel sizing, and no-subpixel policy to Qt. Application packages that cannot
see the platform SDK remain self-contained by using the existing build overlay
to copy `msys-sdk/msys_sdk` beside their entrypoint; no daemon, `pip`, or target
package-manager step is involved.

### Responsive page basics

The optional UI layout helpers fix common narrow/rotated-screen failures
without imposing a visual framework:

```python
from msys_sdk import TkScrollablePage, bind_tk_text_wrap

page = TkScrollablePage(parent)
description = ttk.Label(page.content, text=long_text, justify="left")
description.pack(fill="x")
bind_tk_text_wrap(description, page.canvas)
page.bind_touch_scroll(page.content)
page.pack(fill="both", expand=True)
```

`configure_qt_scroll_area()` and `configure_qt_text_wrap()` provide the same
small policy for Qt widgets. `content_width()` and `responsive_columns()` are
toolkit-neutral for custom/native layouts. There is no global input binding or
UI daemon; scrolling stays owned by the page.

### Optional Tk input-method lifecycle

Tk applications with editable fields can opt into the replaceable
`role:input-method` without naming the stock touch keyboard:

```python
from msys_sdk import TkInputMethodBinding

# Construct this after the private MsysClient is ready. client.call is the
# already-authorized component call method, not a D-Bus or toolkit service.
input_method = TkInputMethodBinding(root, client.call)
input_method.bind(editor, mode="zh")

# Explicit Save/submit actions may dismiss it without closing the editor.
input_method.hide()

def close_window():
    input_method.close()  # ordered best-effort hide, then unbind
    root.destroy()
```

The component manifest needs only the normal application state permissions
plus `mipc.call:role:input-method`. Focus, a real editor touch, focus loss,
outside touch, and widget destruction are coalesced on a small worker. The
default cold-show deadline is six seconds and FocusOut settles for 80 ms, so
on-demand Tk startup and transient focus movement do not become false failures
or duplicate calls. Call `sync_focus()` after delayed client readiness if the
editor was focused first. `bind_tk_input_method()` is the one-editor
convenience form.

This helper imports no Tk module and is entirely optional. Qt, Electron,
C/C++, and other applications continue to call `role:input-method` directly
or use their own toolkit binding; the SDK does not force a Tk lifecycle on
them.

Calls use the same API for replaceable roles, application interfaces, and
exact components:

```python
reply = client.call_interface(
    "org.example.echo.v1", "echo", {"text": "hello"}, timeout=3
)
client.call_component("org.example.worker:sync", "wake", {})
```

Native clients use `msys_mipc_monotonic_ms()` plus
`msys_mipc_send_call_json()`. The latter carries an absolute deadline and an
explicit idempotency bit, so core can safely fail over read-only requests
without replaying a state-changing call whose outcome is unknown.

## Any-language JSON-lines bridge

An application can participate in the full private component protocol using
only JSON plus standard streams.  This is useful for Electron/Node, Lua, Ruby,
Go prototypes, or any runtime for which a dedicated SDK is unnecessary:

```json
{
  "runtime": "electron",
  "exec": [
    "files/runtime/python/bin/python3",
    "-m", "msys_sdk.stdio_bridge", "--",
    "files/runtime/electron/electron", "files/app"
  ],
  "readiness": {"mode": "mipc-ready", "timeout_ms": 8000}
}
```

The wrapped process reads one mIPC JSON object per line from stdin and writes
one object per line to stdout.  It performs the normal `hello` / `welcome` /
`ready` exchange itself and may then call, subscribe, publish, or serve inbound
calls exactly like a native component.  stdout is protocol-only; application
logs go to stderr.  `MSYS_CONTROL_FD` is deliberately removed from the child,
and `MSYS_MIPC_TRANSPORT=stdio-jsonl-v1` identifies the stream contract.

The bridge validates UTF-8, JSON object shape, record boundaries, and the
256-KiB protocol limit in both directions.  A malformed line or a child that
closes its protocol output while continuing to run fails the component instead
of leaving it falsely ready.  Use the installed `msys-mipc-stdio -- command`
entry point or `python3 -m msys_sdk.stdio_bridge -- command`.

[`example/node_stdio_component.js`](example/node_stdio_component.js) is a
dependency-free Node implementation of handshake, subscription, shutdown, and
an inbound `ping` RPC. The exact same stream code can run in Electron's main
process while its renderer remains an ordinary UI implementation detail.
