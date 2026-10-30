"""A URL host the standard parser rejects (a bracketed redaction placeholder) must not abort the sweep: it is still recorded (fail closed)."""

from ace_playbook.clean.bashwrite import bash_analyse
from ace_playbook.clean.origins import written_paths

CWD = "/w/p"


def _dests(raw: set[str]) -> set[str]:
    return {r[1:] for r in raw if r.startswith("\0") and r != "\0\0"}


def test_bracketed_placeholder_host_is_recorded():
    raw, ok = bash_analyse("wget -r https://[REDACTED]/docs")  # urlparse: "'REDACTED' does not appear to be an IPv4 or IPv6 address"
    assert ok and {"[REDACTED]", "[REDACTED]/docs"} <= _dests(raw)
    assert {"wget/[REDACTED]", "wget/[REDACTED]/docs"} <= _dests(raw)


def test_written_paths_for_a_bash_tool_call_records_the_host_under_the_cwd():
    paths, unparsed = written_paths("Bash", {"command": "wget -r https://[REDACTED]/docs"}, CWD, CWD)
    assert not unparsed
    assert {f"{CWD}/[REDACTED]", f"{CWD}/[REDACTED]/docs", f"{CWD}/docs"} <= paths


def test_unclosed_bracket_host_does_not_raise_and_records_the_basename():
    raw, ok = bash_analyse("curl -O http://[abc/file")  # urlparse: "Invalid IPv6 URL"
    assert ok and "file" in raw and "[abc" in _dests(raw)


def test_stray_closing_bracket_host_keeps_the_redirect_target():
    raw, ok = bash_analyse("curl https://host]/x > out.txt")
    assert ok and {"out.txt", "x"} <= raw and {"host]", "host]/x"} <= _dests(raw)


def test_query_is_not_part_of_the_basename_of_a_rejected_url():
    raw, _ = bash_analyse("curl -O http://[abc/file?x=1#f")
    assert "file" in raw


def test_fragment_only_is_not_part_of_the_basename_of_a_rejected_url():
    raw, _ = bash_analyse("curl -O http://[abc/file#f")
    assert "file" in raw and "file#f" not in raw


def test_valid_ipv6_literal_is_unchanged():
    raw, ok = bash_analyse("wget -r http://[::1]/a/b")
    assert ok and raw == {"\0\0", "\0[::1]", "\0[::1]/a", "\0a", "\0wget/[::1]", "\0wget/[::1]/a", "\0wget/a", "b", "wget", "wget/b"}


def test_control_plain_host_is_unchanged():
    raw, ok = bash_analyse("wget -r https://example.com/a/b")
    assert ok and raw == {"\0a", "\0example.com", "\0example.com/a", "\0wget/a", "\0wget/example.com", "\0wget/example.com/a", "b", "wget",
                          "wget/b"}
    paths, unparsed = written_paths("Bash", {"command": "wget -r https://example.com/a/b"}, CWD, CWD)
    assert not unparsed and paths == {f"{CWD}/{p}" for p in (
        "a", "b", "example.com", "example.com/a", "wget", "wget/a", "wget/b", "wget/example.com", "wget/example.com/a")}
