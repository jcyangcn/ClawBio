"""Tests for clawbio.common.textio — cross-OS LF-only bundle writes."""

from pathlib import Path

from clawbio.common.textio import write_text_lf


def test_writes_lf_for_plain_content(tmp_path):
    p = tmp_path / "f.txt"
    write_text_lf(p, "a\nb\nc\n")
    assert p.read_bytes() == b"a\nb\nc\n"


def test_normalises_crlf_to_lf(tmp_path):
    p = tmp_path / "f.txt"
    write_text_lf(p, "a\r\nb\r\nc\r\n")
    raw = p.read_bytes()
    assert b"\r" not in raw
    assert raw == b"a\nb\nc\n"


def test_normalises_lone_cr_to_lf(tmp_path):
    p = tmp_path / "f.txt"
    write_text_lf(p, "a\rb\r")
    assert p.read_bytes() == b"a\nb\n"


def test_preserves_utf8(tmp_path):
    p = tmp_path / "f.txt"
    write_text_lf(p, "café — Ångström\n")
    assert p.read_bytes() == "café — Ångström\n".encode("utf-8")
    assert b"\r" not in p.read_bytes()


def test_returns_path(tmp_path):
    p = tmp_path / "f.txt"
    assert write_text_lf(p, "x\n") == p


def test_accepts_str_path(tmp_path):
    p = tmp_path / "f.txt"
    write_text_lf(str(p), "x\n")
    assert p.read_bytes() == b"x\n"
