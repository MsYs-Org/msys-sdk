"""Build-time C header generator for ``msys.i18n.catalog.v1`` resources."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tempfile
from typing import Sequence

from .i18n import Catalog


_C_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _c_string(value: str) -> str:
    pieces: list[str] = []
    ascii_text: list[str] = []

    def flush() -> None:
        if ascii_text:
            pieces.append('"' + "".join(ascii_text) + '"')
            ascii_text.clear()

    for character in value:
        code = ord(character)
        if 0x20 <= code <= 0x7E:
            if character in {'"', "\\"}:
                ascii_text.append("\\" + character)
            else:
                ascii_text.append(character)
        elif character == "\n":
            ascii_text.append("\\n")
        elif character == "\r":
            ascii_text.append("\\r")
        elif character == "\t":
            ascii_text.append("\\t")
        else:
            flush()
            pieces.extend('"\\x%02x"' % byte for byte in character.encode("utf-8"))
    flush()
    return " ".join(pieces) if pieces else '""'


def render_c_header(catalog: Catalog, symbol: str = "msys_catalog") -> str:
    """Render one validated catalog as a self-contained, UTF-8 C header."""

    if not isinstance(catalog, Catalog):
        raise TypeError("catalog must be a validated Catalog")
    if _C_IDENTIFIER.fullmatch(symbol) is None:
        raise ValueError("symbol must be a C identifier")
    guard = "MSYS_GENERATED_%s_H" % re.sub(r"[^A-Za-z0-9]", "_", symbol).upper()
    entries = [
        (locale, key, value)
        for locale, messages in catalog.messages.items()
        for key, value in messages.items()
    ]
    lines = [
        "/* Generated from msys.i18n.catalog.v1; edit the JSON source. */",
        "#ifndef %s" % guard,
        "#define %s" % guard,
        "",
        "#include <msys/i18n.h>",
        "",
        "static const msys_i18n_entry %s_entries[] = {" % symbol,
    ]
    lines.extend(
        "    {%s, %s, %s},"
        % (_c_string(locale), _c_string(key), _c_string(value))
        for locale, key, value in entries
    )
    lines.extend(
        [
            "};",
            "",
            "static const msys_i18n_catalog %s = {" % symbol,
            "    %s," % _c_string(catalog.id),
            "    %s," % _c_string(catalog.default_locale),
            "    %s_entries," % symbol,
            "    sizeof(%s_entries) / sizeof(%s_entries[0])" % (symbol, symbol),
            "};",
            "",
            "#endif",
            "",
        ]
    )
    return "\n".join(lines)


def write_c_header(catalog_path: Path, output: Path, symbol: str) -> None:
    """Validate and atomically write a generated C catalog header."""

    rendered = render_c_header(Catalog.load(catalog_path), symbol)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % output.name,
        suffix=".tmp",
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with open(descriptor, "w", encoding="utf-8", newline="\n", closefd=True) as handle:
            handle.write(rendered)
            handle.flush()
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="generate a dependency-free C header from an MSYS i18n catalog"
    )
    parser.add_argument("catalog", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--symbol", default="msys_catalog")
    args = parser.parse_args(argv)
    try:
        write_c_header(args.catalog, args.output, args.symbol)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["render_c_header", "write_c_header"]
