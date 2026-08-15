"""The webclaw wrapper.

Nothing here touches the network. What is worth pinning is the wrapper's
contract with its callers, and in particular the two ways a fetch helper
quietly poisons a fixture: by returning a different format than the caller
asked for, and by turning a failed fetch into an empty-but-successful one.
Either produces a fixture that looks fine and is wrong.

Live extraction is covered by `python scripts/webclaw_fetch.py --self-test`,
which is a build-time check rather than a unit test because it depends on two
third-party sites being up.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from scripts import webclaw_fetch as wf


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class _FakeResponse:
    """Just enough of an http.client.HTTPResponse for `with urlopen(...)`."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def installed(monkeypatch):
    """Pretend a binary exists, and hand back the argv it was called with."""
    monkeypatch.setattr(wf, "binary", lambda: "/fake/webclaw")
    monkeypatch.setattr(wf, "version", lambda: "webclaw 0.6.19")
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return _Proc(stdout="# Heading\n\n| band | discount |\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_a_missing_binary_is_an_error_not_a_fallback(monkeypatch):
    """The important one. If this returned raw HTML instead of raising, a
    caller that asked for markdown would parse tag soup into a fixture and
    never know -- which is the exact failure the hand-curated fixtures exist
    to prevent."""
    monkeypatch.setattr(wf, "binary", lambda: None)

    with pytest.raises(wf.WebclawMissing) as exc:
        wf.fetch("https://example.com")
    # And it says how to fix it, since the reader is a teammate on a fresh
    # clone who has never heard of this tool.
    assert "WEBCLAW_BIN" in str(exc.value)


def test_the_two_paths_are_distinguishable_by_their_result(monkeypatch, installed):
    """`fetch_raw_html` is allowed -- it just has to be asked for by name and
    has to admit what it returned. If both paths reported the same `via` and
    `format`, a caller could not tell markdown from tag soup."""
    monkeypatch.setattr(
        wf.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(b"<html><table><tr><td>Flat 2500</td></tr></table></html>"))

    clawed = wf.fetch("https://example.com")
    plain = wf.fetch_raw_html("https://example.com")

    assert (clawed["via"], clawed["format"]) == ("webclaw", "llm")
    assert (plain["via"], plain["format"]) == ("urllib", "html")
    assert plain["text"].startswith("<html>")


def test_a_nonzero_exit_raises_and_carries_the_stderr(monkeypatch):
    """A blocked or 404'd page must not come back as a successful empty
    string: an empty extraction silently empties whatever it feeds."""
    monkeypatch.setattr(wf, "binary", lambda: "/fake/webclaw")
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: _Proc(stderr="dns error: no such host",
                                                 returncode=2))

    with pytest.raises(RuntimeError, match="exited 2"):
        wf.fetch("https://nope.invalid")


def test_a_bad_format_is_rejected_before_the_process_starts(monkeypatch):
    monkeypatch.setattr(wf, "binary", lambda: "/fake/webclaw")
    with pytest.raises(ValueError, match="format must be one of"):
        wf.fetch("https://example.com", fmt="pdf")


def test_main_content_is_stripped_by_default_and_keepable(installed):
    """SBI's Bhashini translation disclaimer and the whole nav open every
    single extraction otherwise, and they are identical on all nine offers."""
    wf.fetch("https://example.com")
    assert "--only-main-content" in installed[0]

    wf.fetch("https://example.com", only_main_content=False)
    assert "--only-main-content" not in installed[1]


def test_the_result_says_which_extractor_produced_it(installed):
    """Downstream must never have to guess whether it holds markdown or HTML."""
    result = wf.fetch("https://example.com")

    assert result["via"] == "webclaw"
    assert result["format"] == "llm"
    assert result["tool_version"] == "webclaw 0.6.19"
    assert result["chars"] == len(result["text"])
    # Provenance a fixture can cite without a second lookup.
    assert result["url"] == "https://example.com"
    assert result["fetched_at"].endswith("+00:00")


def test_json_mode_surfaces_the_markdown_and_the_metadata(monkeypatch):
    """webclaw's json puts the prose under content.markdown and the page's own
    title/language beside it -- which is the `as_of`/`source_name` envelope
    the fixtures already carry, arriving for free."""
    monkeypatch.setattr(wf, "binary", lambda: "/fake/webclaw")
    monkeypatch.setattr(wf, "version", lambda: "webclaw 0.6.19")
    payload = {
        "metadata": {"title": "Debit Card Offers", "language": "en-US", "word_count": 1253},
        "content": {"markdown": "| Above Rs 49,999 Upto Rs 99,999 | Flat 2500 |"},
    }
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **kw: _Proc(stdout=json.dumps(payload)))

    result = wf.fetch("https://example.com", fmt="json")

    assert result["metadata"]["title"] == "Debit Card Offers"
    assert "Flat 2500" in result["text"]
    assert result["data"] == payload


def test_unparseable_json_is_an_error(monkeypatch):
    monkeypatch.setattr(wf, "binary", lambda: "/fake/webclaw")
    monkeypatch.setattr(wf, "version", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _Proc(stdout="not json"))

    with pytest.raises(RuntimeError, match="unparseable"):
        wf.fetch("https://example.com", fmt="json")


def test_the_child_process_is_decoded_as_utf8(monkeypatch):
    """Without an explicit encoding Python decodes the child's stdout with the
    Windows ANSI codepage, and every rupee sign and Devanagari character in a
    page comes back mojibake. This repo has been bitten by exactly that once
    already, over curl mangling Tamil."""
    monkeypatch.setattr(wf, "binary", lambda: "/fake/webclaw")
    monkeypatch.setattr(wf, "version", lambda: None)
    captured: dict = {}

    def recording_run(argv, **kwargs):
        captured.update(kwargs)
        return _Proc(stdout="₹2,500 — डेबिट कार्ड")

    monkeypatch.setattr(subprocess, "run", recording_run)
    result = wf.fetch("https://example.com")

    assert captured.get("encoding") == "utf-8"
    assert captured.get("text") is True
    assert "₹2,500" in result["text"] and "डेबिट" in result["text"]


def test_webclaw_binary_env_override_is_searched_first(monkeypatch, tmp_path):
    """A teammate on a different layout, or anyone testing a new version,
    must not have to edit the module."""
    fake = tmp_path / "webclaw.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("WEBCLAW_BIN", str(fake))
    # _CANDIDATES is read at import time, so rebuild it the way the module does.
    monkeypatch.setattr(wf, "_CANDIDATES", (str(fake), "webclaw"))

    assert wf.binary() == str(fake)
    assert wf.available() is True


class TestItStaysOutOfTheRequestPath:
    """build_doc_index.py's docstring states that nothing in the request path
    fetches a web page -- a live fetch inside a chat turn puts a third party's
    uptime and latency in the user's way. webclaw ships an MCP server and a
    REST server, both of which invite exactly that, so the boundary is worth
    a test rather than a comment.
    """

    def test_no_runtime_package_imports_it(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        offenders = []
        for package in ("agent_platform", "capabilities_impl", "backend"):
            for path in (repo / package).rglob("*.py"):
                if "webclaw" in path.read_text(encoding="utf-8", errors="ignore"):
                    offenders.append(str(path.relative_to(repo)))

        assert offenders == [], (
            "webclaw is a build-time tool; these runtime modules reference it: "
            f"{offenders}"
        )
