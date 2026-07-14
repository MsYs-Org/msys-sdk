from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence


PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$")
COMPONENT_ID_RE = re.compile(r"^[a-z][a-z0-9._-]*$")
VERSION_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
ROLE_RE = re.compile(r"^[a-z][a-z0-9.-]*$")
INTERFACE_RE = re.compile(r"^[a-z][a-z0-9._-]*$")
CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9._:-]*$")
PERMISSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@*+\-]*$")

SUPPORTED_RUNTIMES = ("python", "tk", "c", "cpp", "qt", "electron")
LIFECYCLES = ("session", "background", "on-demand", "manual")
RESTART_POLICIES = ("never", "on-failure", "always")
PACKAGE_KINDS = ("application", "system", "driver", "tool")
READINESS_MODES = ("exec", "mipc-ready")
WINDOW_MODES = ("window", "fullscreen", "overlay", "background")


class AppManifestError(ValueError):
    """A developer-facing application declaration could not be generated."""


def _validate_identifier(value: str, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AppManifestError(f"invalid {label}: {value!r}")
    return value


def _validate_text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise AppManifestError(f"{label} must contain 1..{maximum} characters")
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise AppManifestError(f"{label} contains a control character")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise AppManifestError(f"{label} is not valid UTF-8") from exc
    return value


def _validate_bool(value: bool, label: str) -> bool:
    if not isinstance(value, bool):
        raise AppManifestError(f"{label} must be a boolean")
    return value


def _unique(values: Iterable[str], label: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise AppManifestError(f"{label} values must be an iterable of strings")
    result: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise AppManifestError(
            f"{label} values must be an iterable of strings"
        ) from exc
    for value in iterator:
        if not isinstance(value, str):
            raise AppManifestError(f"{label} values must be strings")
        if value in seen:
            raise AppManifestError(f"duplicate {label}: {value}")
        seen.add(value)
        result.append(value)
    return result


def _strings(values: Iterable[str], label: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise AppManifestError(f"{label} values must be an iterable of strings")
    try:
        result = list(values)
    except TypeError as exc:
        raise AppManifestError(
            f"{label} values must be an iterable of strings"
        ) from exc
    if any(not isinstance(value, str) for value in result):
        raise AppManifestError(f"{label} values must be strings")
    return result


def _default_name(package_id: str) -> str:
    leaf = package_id.rsplit(".", 1)[-1].replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in leaf.split()) or "MSYS App"


def _runtime_command(runtime: str) -> tuple[list[str], dict[str, str], int]:
    if runtime in {"python", "tk"}:
        return (
            ["python", "@package/files/app/main.py"],
            {"PYTHONUNBUFFERED": "1"},
            5000,
        )
    if runtime in {"c", "cpp"}:
        return (["@package/files/bin/app"], {}, 5000)
    if runtime == "qt":
        return (
            ["@package/files/bin/app"],
            {
                "QT_QPA_PLATFORM": "xcb",
                "QT_PLUGIN_PATH": "files/runtime/qt/plugins",
                "LD_LIBRARY_PATH": "files/runtime/qt/lib",
            },
            8000,
        )
    return (
        [
            "@package/files/runtime/electron/electron",
            "--no-sandbox",
            "@package/files/app",
        ],
        {"ELECTRON_ENABLE_LOGGING": "1"},
        10000,
    )


def create_application_manifest(
    *,
    package_id: str,
    runtime: str,
    name: str | None = None,
    version: str = "0.1.0",
    component: str = "main",
    kind: str = "application",
    lifecycle: str | None = None,
    restart: str | None = None,
    readiness: str | None = None,
    timeout_ms: int | None = None,
    graphical: bool = True,
    display: str = "inherit",
    window_mode: str = "window",
    launchable: bool | None = None,
    roles: Iterable[str] = (),
    interfaces: Iterable[str] = (),
    capabilities: Iterable[str] = (),
    permissions: Iterable[str] = (),
    entrypoint: str | None = None,
    exec_args: Iterable[str] = (),
) -> dict[str, Any]:
    """Create one strict ``msys.manifest.v1`` application declaration.

    The function only generates the language-neutral declaration. It does not
    download a runtime, create files, or guess role-specific permissions.
    Formal package and contract validation remains the install/tooling layer's
    responsibility.
    """

    package_id = _validate_identifier(package_id, "package id", PACKAGE_ID_RE)
    if len(package_id) > 128:
        raise AppManifestError("package id must contain at most 128 characters")
    component = _validate_identifier(component, "component id", COMPONENT_ID_RE)
    if len(component) > 64:
        raise AppManifestError("component id must contain at most 64 characters")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise AppManifestError(f"invalid semantic version: {version!r}")
    if runtime not in SUPPORTED_RUNTIMES:
        raise AppManifestError(f"unsupported runtime: {runtime!r}")
    if kind not in PACKAGE_KINDS:
        raise AppManifestError(f"unsupported package kind: {kind!r}")

    _validate_bool(graphical, "graphical")
    if launchable is not None:
        _validate_bool(launchable, "launchable")

    display_name = _validate_text(
        _default_name(package_id) if name is None else name,
        "name",
        128,
    )
    role_values = _unique(roles, "role")
    interface_values = _unique(interfaces, "interface")
    capability_values = _unique(capabilities, "capability")
    permission_values = _unique(permissions, "permission")
    for role in role_values:
        _validate_identifier(role, "role", ROLE_RE)
    for interface in interface_values:
        _validate_identifier(interface, "interface", INTERFACE_RE)
    for capability in capability_values:
        _validate_identifier(capability, "capability", CAPABILITY_RE)
    for permission in permission_values:
        _validate_identifier(permission, "permission", PERMISSION_RE)

    # A capability-only declaration is still a discoverable provider. Treat
    # every ``provides`` kind consistently so it is supervised and ready
    # before discovery returns it to another component.
    active_provider = bool(role_values or interface_values or capability_values)
    selected_lifecycle = lifecycle or ("on-demand" if active_provider else "manual")
    if selected_lifecycle not in LIFECYCLES:
        raise AppManifestError(f"unsupported lifecycle: {selected_lifecycle!r}")
    selected_restart = restart or (
        "never" if selected_lifecycle == "manual" else "on-failure"
    )
    if selected_restart not in RESTART_POLICIES:
        raise AppManifestError(f"unsupported restart policy: {selected_restart!r}")
    selected_readiness = readiness or ("mipc-ready" if active_provider else "exec")
    if selected_readiness not in READINESS_MODES:
        raise AppManifestError(f"unsupported readiness mode: {selected_readiness!r}")

    default_command, environment, default_timeout = _runtime_command(runtime)
    command = list(default_command)
    if entrypoint is not None:
        command = [_validate_text(entrypoint, "entrypoint", 4096)]
    argument_values = _strings(exec_args, "exec argument")
    for argument in argument_values:
        command.append(_validate_text(argument, "exec argument", 4096))
    selected_timeout = default_timeout if timeout_ms is None else timeout_ms
    if not isinstance(selected_timeout, int) or isinstance(selected_timeout, bool):
        raise AppManifestError("timeout must be an integer")
    if not 1 <= selected_timeout <= 300000:
        raise AppManifestError("timeout must be between 1 and 300000 ms")

    if graphical:
        if window_mode not in WINDOW_MODES:
            raise AppManifestError(f"unsupported window mode: {window_mode!r}")
        if display != "inherit" and re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", display) is None:
            raise AppManifestError("display must be 'inherit' or an X11 display such as :24")
        if "display:x11" not in permission_values:
            permission_values.append("display:x11")

    provides: list[dict[str, Any]] = []
    provides.extend({"role": value, "exclusive": True} for value in role_values)
    provides.extend(
        {"interface": value, "exclusive": False} for value in interface_values
    )
    provides.extend(
        {"capability": value, "exclusive": False} for value in capability_values
    )

    if launchable is None:
        launchable = (
            graphical
            and selected_lifecycle == "manual"
            and not active_provider
        )
    component_item: dict[str, Any] = {
        "id": component,
        "name": display_name,
        "runtime": runtime,
        "exec": command,
        "lifecycle": selected_lifecycle,
        "restart": selected_restart,
        "readiness": {"mode": selected_readiness, "timeout_ms": selected_timeout},
        "isolation": "baseline",
        "activation": {"launchable": bool(launchable)},
    }
    if environment:
        component_item["env"] = environment
    if provides:
        component_item["provides"] = provides
    if graphical:
        component_item["windowing"] = {
            "system": "x11",
            "display": display,
            "mode": window_mode,
            "title": display_name,
            "identity": {
                "app_id": package_id,
                "x11_wm_class": package_id,
                "x11_wm_instance": component,
            },
        }
    if permission_values:
        component_item["permissions"] = permission_values

    return {
        "schema": "msys.manifest.v1",
        "package": {
            "id": package_id,
            "name": display_name,
            "version": version,
            "kind": kind,
            "summary": f"{display_name}, an MSYS {runtime} component",
        },
        "components": [component_item],
    }


def write_manifest(
    manifest: dict[str, Any], destination: str | os.PathLike[str], *, force: bool = False
) -> Path:
    """Atomically write a generated manifest without overwriting by default."""

    path = Path(destination)
    if path.exists() and not force:
        raise AppManifestError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_name = stream.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="msys-app-manifest",
        description="Generate one dependency-free MSYS application declaration.",
    )
    parser.add_argument("--id", dest="package_id", required=True)
    parser.add_argument("--runtime", required=True, choices=SUPPORTED_RUNTIMES)
    parser.add_argument("--name", help="display name; defaults to the app id leaf")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--component", default="main")
    parser.add_argument("--kind", choices=PACKAGE_KINDS, default="application")
    parser.add_argument("--lifecycle", choices=LIFECYCLES)
    parser.add_argument("--restart", choices=RESTART_POLICIES)
    parser.add_argument("--readiness", choices=READINESS_MODES)
    parser.add_argument("--timeout-ms", type=int)
    parser.add_argument("--headless", action="store_true", help="omit X11 window metadata")
    parser.add_argument("--display", default="inherit")
    parser.add_argument("--window-mode", choices=WINDOW_MODES, default="window")
    launch_group = parser.add_mutually_exclusive_group()
    launch_group.add_argument("--launchable", action="store_true", dest="launchable")
    launch_group.add_argument("--no-launcher", action="store_false", dest="launchable")
    parser.set_defaults(launchable=None)
    parser.add_argument("--role", action="append", default=[])
    parser.add_argument("--interface", action="append", default=[])
    parser.add_argument("--capability", action="append", default=[])
    parser.add_argument("--permission", action="append", default=[])
    parser.add_argument("--entrypoint", help="replace the runtime's default executable")
    parser.add_argument(
        "--arg", action="append", default=[], help="append one literal argv item; repeatable"
    )
    parser.add_argument("-o", "--output", default="manifest.json", help="path or '-' for stdout")
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        manifest = create_application_manifest(
            package_id=args.package_id,
            runtime=args.runtime,
            name=args.name,
            version=args.version,
            component=args.component,
            kind=args.kind,
            lifecycle=args.lifecycle,
            restart=args.restart,
            readiness=args.readiness,
            timeout_ms=args.timeout_ms,
            graphical=not args.headless,
            display=args.display,
            window_mode=args.window_mode,
            launchable=args.launchable,
            roles=args.role,
            interfaces=args.interface,
            capabilities=args.capability,
            permissions=args.permission,
            entrypoint=args.entrypoint,
            exec_args=args.arg,
        )
        if args.output == "-":
            json.dump(manifest, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            path = write_manifest(manifest, args.output, force=args.force)
            print(f"created {path}")
    except AppManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
