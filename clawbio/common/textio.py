"""Cross-platform text writing for reproducibility bundles.

Every text artifact in a reproducibility bundle (commands.sh, params.yaml,
provenance JSONs, environment.yml, checksums, configs) must be byte-identical
regardless of the OS that generated it. Python's ``Path.write_text`` /
``open("w")`` use ``newline=None`` by default, which translates ``\\n`` to
``os.linesep`` — emitting CRLF on Windows. CRLF then:

  * breaks ``commands.sh`` (a ``\\r`` leaks into shell variables / line
    continuations under bash),
  * changes every file's sha256 (so ``manifest.json`` checksums differ between
    a macOS/Linux-generated bundle and a Windows-generated one),
  * makes ``sha256sum -c checksums.sha256`` fail.

``write_text_lf`` writes *bytes* after normalising line endings to LF, so no
OS-level newline translation can reintroduce ``\\r``. This is the single
choke-point all bundle writers route through.
"""

from __future__ import annotations

from pathlib import Path


def write_text_lf(path: Path | str, text: str) -> Path:
    """Write ``text`` to ``path`` with LF line endings on every OS.

    CRLF and lone-CR sequences in ``text`` are normalised to LF, then the
    result is written as UTF-8 bytes (bypassing text-mode newline translation).
    Returns the written path.
    """
    p = Path(path)
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    p.write_bytes(normalised.encode("utf-8"))
    return p
