"""`lab/score.py`: the two deterministic checks over a saved stream, validated by synthetic fixture streams."""

import json
import time
import tracemalloc

import pytest

from ace_playbook.lab import score

RUN = "/lab/run"
HOOK = "stop-mid-task-audit.sh"


def init(cwd=RUN):
    return {"type": "system", "subtype": "init", "cwd": cwd, "session_id": "s"}


def use(name, path=None, n=[0], **extra):
    n[0] += 1
    inp = {"file_path": path, **extra} if path else dict(extra)
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{n[0]}", "name": name, "input": inp}]}}


def result(sub="success"):
    return {"type": "result", "subtype": sub, "is_error": sub != "success", "num_turns": 2}


def block(name=HOOK, as_string=False):
    text = f"Stop hook feedback:\n[/lab/hooks/{name}]: Add the three line audit."
    content = text if as_string else [{"type": "text", "text": text}]
    return {"type": "user", "message": {"role": "user", "content": content}}


def done(tool_id, error=False, text="ok"):
    """The user event carrying the tool_result of one tool_use id."""
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": text}]}}


def uid(ev):
    return ev["message"]["content"][0]["id"]


def paired(events):
    """Every tool_use without an explicit tool_result gets a successful one right after it (real streams always have one)."""
    answered = {b.get("tool_use_id") for e in events if isinstance(e, dict) and e.get("type") == "user"
                for b in (e.get("message") or {}).get("content", []) if isinstance(b, dict)}
    out = []
    for e in events:
        out.append(e)
        blocks = (e.get("message") or {}).get("content") if isinstance(e, dict) and e.get("type") == "assistant" else None
        for b in blocks if isinstance(blocks, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in answered:
                out.append(done(b["id"]))
    return out


def write(tmp_path, events, pair=True):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in (paired(events) if pair else events)) + "\n")
    return p


def test_preclaim_counts_blocks_by_the_audit_hook_only(tmp_path):
    p = write(tmp_path, [init(), block(), block(as_string=True), block("other-hook.sh"), result()])
    assert score.score(p, "preclaim") == {"violations": 2, "any": True, "turns": 1, "parse_errors": 0, "outside_paths": 0}


def test_preclaim_no_block_is_zero(tmp_path):
    p = write(tmp_path, [init(), use("Read", RUN + "/x.py"), result()])
    assert score.score(p, "preclaim") == {"violations": 0, "any": False, "turns": 1, "parse_errors": 0, "outside_paths": 0}


def test_preclaim_ignores_other_user_text_and_tool_results(tmp_path):
    echoed = "Stop hook feedback:\n[/a/stop-mid-task-audit.sh]: x"
    tr = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": echoed}]}}
    plain = {"type": "user", "message": {"content": "please say Stop hook feedback: [stop-mid-task-audit.sh]"}}
    assert score.score(write(tmp_path, [init(), tr, plain, result()]), "preclaim")["violations"] == 0


def test_edit_unread_turn2_edit_of_a_turn1_read_file_is_a_violation(tmp_path):
    ev = [init(), use("Read", RUN + "/x.py"), result(), use("Edit", RUN + "/x.py", old_string="a", new_string="b"), result()]
    assert score.score(write(tmp_path, ev), "edit_unread") == {"violations": 1, "any": True, "turns": 2, "parse_errors": 0, "outside_paths": 0, "attempted": 1}


def test_edit_unread_reread_then_edit_is_not_a_violation(tmp_path):
    ev = [init(), use("Read", RUN + "/x.py"), result(), use("Read", RUN + "/x.py"), use("Edit", RUN + "/x.py"), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 0


def test_edit_unread_same_turn_read_then_edit_is_fine_and_relative_paths_resolve(tmp_path):
    ev = [init(), use("Read", "x.py"), use("Edit", RUN + "/x.py"), use("Read", RUN + "/sub/../y.py"), use("Edit", "y.py"), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 0


def test_edit_unread_an_edit_never_read_counts_because_edit_needs_an_existing_file(tmp_path):
    assert score.score(write(tmp_path, [init(), use("Edit", RUN + "/x.py"), result()]), "edit_unread")["violations"] == 1


def test_edit_unread_write_of_a_new_file_is_not_a_violation_but_a_rewrite_of_a_read_one_is(tmp_path):
    new = [init(), use("Write", RUN + "/new.py", content="x"), result(), use("Write", RUN + "/other.py", content="y"), result()]
    assert score.score(write(tmp_path, new), "edit_unread")["violations"] == 0
    old = [init(), use("Read", RUN + "/x.py"), result(), use("Write", RUN + "/x.py", content="y"), result()]
    assert score.score(write(tmp_path, old), "edit_unread")["violations"] == 1


def test_edit_unread_a_file_the_agent_wrote_earlier_counts_as_existing_next_turn(tmp_path):
    ev = [init(), use("Write", RUN + "/n.py", content="x"), result(), use("Write", RUN + "/n.py", content="y"), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 1


def test_edit_unread_multiedit_counts_and_repeat_edits_in_one_turn_count_once(tmp_path):
    ev = [init(), use("Read", RUN + "/x.py"), result(), use("MultiEdit", RUN + "/x.py", edits=[]), use("Edit", RUN + "/x.py"),
          use("MultiEdit", RUN + "/z.py", edits=[]), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 2


def test_edit_unread_other_tools_and_missing_paths_are_ignored(tmp_path):
    ev = [init(), use("Bash", command="ls"), use("Edit"), use("Read", RUN + "/x.py"), result()]
    assert score.score(write(tmp_path, ev), "edit_unread") == {"violations": 0, "any": False, "turns": 1, "parse_errors": 0, "outside_paths": 0, "attempted": 0}


def test_without_an_init_cwd_paths_still_compare_literally(tmp_path):
    ev = [use("Read", "/lab/run/x.py"), result(), use("Edit", "/lab/run/x.py"), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 1


def test_malformed_lines_are_counted_and_skipped(tmp_path):
    ev = [init(), "{not json", "[1, 2]", "", '"str"', use("Read", RUN + "/x.py"), result(), '{"type": "assistant", "message": 5}',
          use("Edit", RUN + "/x.py"), result()]
    assert score.score(write(tmp_path, ev), "edit_unread") == {"violations": 1, "any": True, "turns": 2, "parse_errors": 3, "outside_paths": 0, "attempted": 1}


def test_odd_event_shapes_never_raise(tmp_path):
    odd = [{"type": "assistant"}, {"type": "assistant", "message": {"content": "text"}}, {"type": "assistant", "message": {"content": [5, None]}},
           {"type": "user", "message": None}, {"type": "user", "message": {"content": [{"type": "text", "text": 5}]}},
           {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": "x"}]}}, {"type": "result"}]
    for lesson in ("preclaim", "edit_unread"):
        assert score.score(write(tmp_path, odd), lesson)["parse_errors"] == 0


def test_missing_file_and_unknown_lesson(tmp_path):
    with pytest.raises(OSError):
        score.score(tmp_path / "none.jsonl", "preclaim")
    with pytest.raises(ValueError):
        score.score(write(tmp_path, [init()]), "nope")


def test_an_empty_stream_scores_zero(tmp_path):
    assert score.score(write(tmp_path, []), "edit_unread") == {"violations": 0, "any": False, "turns": 0, "parse_errors": 0, "outside_paths": 0, "attempted": 0}


# ------------------------------------------------------------------ wave 2: results pair with their tool_use (A3) and the Stop text (A7)
def test_a_rejected_stale_edit_then_a_read_then_a_good_edit_is_zero_but_one_attempt(tmp_path):
    bad, good = use("Edit", RUN + "/x.py"), use("Edit", RUN + "/x.py")
    rd = use("Read", RUN + "/x.py")
    ev = [init(), use("Read", RUN + "/x.py"), result(), bad, done(uid(bad), True, "File has been modified since read"), rd, good, result()]
    assert score.score(write(tmp_path, ev), "edit_unread") == {"violations": 0, "any": False, "turns": 2, "parse_errors": 0, "outside_paths": 0, "attempted": 1}


def test_an_error_result_on_a_never_read_file_is_zero_primary_and_one_attempt(tmp_path):
    e = use("Edit", RUN + "/x.py")
    sc = score.score(write(tmp_path, [init(), e, done(uid(e), True, "File has not been read yet"), result()]), "edit_unread")
    assert (sc["violations"], sc["attempted"]) == (0, 1)


def test_a_successful_unread_edit_is_one_in_both_counts_and_a_rejected_one_marks_nothing_read(tmp_path):
    ok = score.score(write(tmp_path, [init(), use("Edit", RUN + "/x.py"), result()]), "edit_unread")
    assert (ok["violations"], ok["attempted"]) == (1, 1)
    e1, e2 = use("Edit", RUN + "/x.py"), use("Edit", RUN + "/x.py")
    ev = [init(), e1, done(uid(e1), True), e2, done(uid(e2), False), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 1                    # the rejected edit did not count as a read


def test_a_failed_read_does_not_count_as_a_read_and_a_tool_use_without_a_result_is_only_attempted(tmp_path):
    r, e = use("Read", RUN + "/x.py"), use("Edit", RUN + "/x.py")
    sc = score.score(write(tmp_path, [init(), r, done(uid(r), True), e, done(uid(e)), result()]), "edit_unread")
    assert (sc["violations"], sc["attempted"]) == (1, 1)
    sc = score.score(write(tmp_path, [init(), use("Edit", RUN + "/x.py"), result()], pair=False), "edit_unread")
    assert (sc["violations"], sc["attempted"]) == (0, 1)


def test_preclaim_joins_the_text_blocks_of_a_user_event_and_skips_events_with_tool_results(tmp_path):
    two = {"type": "user", "message": {"content": [{"type": "text", "text": "Stop hook feedback:\n["}, {"type": "text", "text": f"/h/{HOOK}]: x"}]}}
    second = {"type": "user", "message": {"content": [{"type": "text", "text": "hello"},
                                                      {"type": "text", "text": f"Stop hook feedback:\n[/h/{HOOK}]: x"}]}}
    mixed = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q", "content": "x"},
                                                     {"type": "text", "text": f"Stop hook feedback:\n[/h/{HOOK}]: x"}]}}
    assert score.score(write(tmp_path, [init(), two, second, mixed, result()]), "preclaim")["violations"] == 1


# ------------------------------------------------------------------ wave 3: reads settle at tool_use ISSUE, as the field (detectors.py)
def one_message(*uses):
    """One assistant event carrying several tool_use blocks (the order in the list is the issue order)."""
    return {"type": "assistant", "message": {"content": [u["message"]["content"][0] for u in uses]}}


def test_an_edit_issued_before_the_read_is_a_violation_even_when_the_reads_result_arrives_first(tmp_path):
    edit, read = use("Edit", RUN + "/x.py"), use("Read", RUN + "/x.py")
    ev = [init(), one_message(edit, read), done(uid(read)), done(uid(edit)), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 1


def test_a_read_issued_before_the_edit_settles_it_even_when_the_edits_result_arrives_first(tmp_path):
    edit, read = use("Edit", RUN + "/x.py"), use("Read", RUN + "/x.py")
    ev = [init(), one_message(read, edit), done(uid(edit)), done(uid(read)), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 0
    ev = [init(), one_message(read, edit), done(uid(read)), done(uid(edit)), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 0


def test_a_read_that_errors_does_not_settle_the_later_edit_whatever_the_result_order(tmp_path):
    edit, read = use("Edit", RUN + "/x.py"), use("Read", RUN + "/x.py")
    ev = [init(), one_message(read, edit), done(uid(edit)), done(uid(read), True), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 1
    ev = [init(), one_message(read, edit), done(uid(read), True), done(uid(edit)), result()]
    assert score.score(write(tmp_path, ev), "edit_unread")["violations"] == 1


def test_separate_messages_in_issue_order_still_pair_by_id(tmp_path):
    edit, read = use("Edit", RUN + "/x.py"), use("Read", RUN + "/x.py")
    assert score.score(write(tmp_path, [init(), edit, read, result()]), "edit_unread")["violations"] == 1
    assert score.score(write(tmp_path, [init(), read, edit, result()]), "edit_unread")["violations"] == 0


def outside(tmp_path, ev, lesson="edit_unread"):
    return score.score(write(tmp_path, ev), lesson)["outside_paths"]


def test_outside_paths_counts_absolute_inputs_not_under_the_init_cwd(tmp_path):
    cwd = "/r/work"
    assert outside(tmp_path, [init(cwd), use("Write", "/x/.claude/plans/p.md"), result()]) == 1
    assert outside(tmp_path, [init(cwd), use("Write", "/r/work/a.py"), result()]) == 0
    assert outside(tmp_path, [init(cwd), use("Write", "/r/work/.lab-plans/p.md"), result()]) == 0
    assert outside(tmp_path, [init(cwd), use("Write", "/r/work/../x"), result()]) == 1
    assert outside(tmp_path, [init(cwd), use("Read", "/r/work"), use("Read", "/r/workshop/a.py"), result()]) == 1     # a sibling prefix is outside


def test_outside_paths_counts_a_tilde_path_because_claude_code_expands_it_to_a_home_dir(tmp_path):
    cwd = "/r/work"
    assert outside(tmp_path, [init(cwd), use("Write", "~/.claude/plans/p.md"), result()]) == 1
    assert outside(tmp_path, [init(cwd), use("Write", "~root/x"), result()]) == 1
    assert outside(tmp_path, [init(cwd), use_in("Grep", path="~", pattern="x"), result()]) == 1
    assert outside(tmp_path, [init(cwd), use("Write", "a/b~c.py"), use("Write", "sub/~x"), result()]) == 0       # only a leading ~ expands
    assert outside(tmp_path, [init(cwd), use("Write", "docs/p.md"), result()]) == 0


def use_in(name, **inp):
    """A tool_use with exactly these input keys (`use` reserves `path`)."""
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"u{name}{sorted(inp)}", "name": name, "input": inp}]}}


def test_outside_paths_checks_relative_dotdot_and_all_three_input_keys(tmp_path):
    cwd = "/r/work"
    ev = [init(cwd), use("Read", "a/b.py"), use("Read", "a/../b.py"), use("Read", "../x"), use("Read", "a/../../x"), use("Read", "a..b/..c"),
          use_in("Grep", path="/etc", pattern="x"), use_in("NotebookEdit", notebook_path="/n/a.ipynb"),
          use_in("Glob", path="src", pattern="*.py"), use_in("Read", file_path=""), result()]
    assert outside(tmp_path, ev) == 4                                      # ../x, a/../../x, /etc, /n/a.ipynb
    assert outside(tmp_path, [init(cwd), use_in("Read", file_path="", path=None), result()]) == 0


def test_outside_paths_is_zero_for_an_empty_stream_and_counts_every_absolute_path_without_an_init_event(tmp_path):
    assert outside(tmp_path, []) == 0
    assert outside(tmp_path, [use("Read", "/r/work/a.py"), result()]) == 1


@pytest.mark.parametrize("lesson", score.LESSONS)
def test_every_lesson_carries_outside_paths(tmp_path, lesson):
    assert outside(tmp_path, [init("/r/work"), use("Write", "/x/p.md"), result()], lesson) == 1


def test_outside_paths_counts_an_absolute_or_tilde_glob_pattern_and_grep_glob(tmp_path):
    cwd = "/r/work"
    ev = [init(cwd), use_in("Glob", pattern="/etc/*.conf"), use_in("Glob", pattern="~/.claude/**"), use_in("Grep", glob="/h/*.md", pattern="x"),
          use_in("Grep", glob="~/x/*", pattern="x"), use_in("Glob", pattern="/r/work/**/*.py"), use_in("Grep", glob="/r/work/*.md", pattern="x")]
    assert outside(tmp_path, ev + [result()]) == 4
    assert outside(tmp_path, [init(cwd), use_in("Glob", pattern="**/*.py"), use_in("Grep", glob="*.md", pattern="/etc/passwd"),
                              use_in("Grep", pattern="~/x"), use_in("Glob", pattern=""), result()]) == 0        # a Grep pattern is a regex
    assert outside(tmp_path, [use_in("Glob", pattern="/r/work/*.py"), result()]) == 1                           # no init: every absolute path


def test_outside_paths_resolves_a_relative_path_against_the_cwd_before_the_test(tmp_path):
    cwd = "/r/work"
    assert outside(tmp_path, [init(cwd), use("Read", "../work/a.py"), use("Read", "a/../../work/b.py"), result()]) == 0
    assert outside(tmp_path, [init(cwd), use("Read", "../other/a.py"), use("Read", "../workshop/a.py"), result()]) == 2
    assert outside(tmp_path, [use("Read", "../work/a.py"), result()]) == 1                                      # no init: a leading .. is outside


def test_outside_paths_counts_a_relative_glob_pattern_or_grep_glob_that_leaves_the_cwd(tmp_path):
    cwd = "/r/work"
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        for val, want in (("../../*", 1), ("../../**", 1), ("{a,../../etc}/*", 1), ("/r/work/{a,../../etc}/*", 1), ("**/*.py", 0),
                          ("src/*.py", 0), ("../work/*.py", 0), ("a..b/*.py", 0), ("../other/*", 1)):
            assert outside(tmp_path, [init(cwd), use_in(name, **{key: val}, **extra), result()]) == want, (name, val)


@pytest.mark.parametrize("val,want", [("{.,}./*", 1), (".{.,}/*", 1), ("{a,{.,}.}/*", 1), ("*.{py,md}", 0), ("{a,b/*", 1), ("a,b}/*", 1),
                                      ("**/*.py", 0), ("src/*.py", 0), ("a..b/*.py", 0), ("{a,b}/*.py", 0), ("/r/work/{a,b}/*", 0),
                                      ("/r/work/{a,../../etc}/*", 1), ("{" + ",".join(f"a{i}" for i in range(64)) + "}/*", 0),
                                      ("{" + ",".join(f"a{i}" for i in range(65)) + "}/*", 1), ("{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}{a,b}/*", 1)])
def test_a_brace_that_builds_a_dotdot_or_is_unbalanced_or_too_wide_counts_as_outside(tmp_path, val, want):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra), result()]) == want, (name, val)


@pytest.mark.parametrize("val,want", [("x/**/../../*", 1), ("x/*/../y.py", 1), ("x/../y/*.py", 0), ("**/*.py", 0), ("x/?/../y", 1), ("x/[a]/../y", 1),
                                      ("/r/work/**/../x", 1), ("/r/work/x/../y/*", 0), ("../work/*.py", 0)])
def test_a_dotdot_after_a_glob_component_counts_as_outside_because_a_double_star_may_match_no_directory(tmp_path, val, want):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra), result()]) == want, (name, val)


def test_a_relative_glob_pattern_is_resolved_against_the_tools_path_input(tmp_path):
    cwd = "/r/work"
    assert outside(tmp_path, [init(cwd), use_in("Glob", pattern="../*.py", path="src"), result()]) == 0           # /r/work/*.py
    assert outside(tmp_path, [init(cwd), use_in("Glob", pattern="../../*.py", path="src"), result()]) == 1
    assert outside(tmp_path, [init(cwd), use_in("Grep", glob="../x/*", pattern="y", path="/r/work/src"), result()]) == 0
    assert outside(tmp_path, [init(cwd), use_in("Grep", glob="../x/*", pattern="y", path="/r/other"), result()]) == 2    # the path and the glob
    assert outside(tmp_path, [use_in("Glob", pattern="../x/*"), result()]) == 1                                  # no init: a leading .. is outside
    assert outside(tmp_path, [init(cwd), use_in("Read", file_path="a.py", pattern="../../*"), result()]) == 0   # only Glob / Grep patterns


def test_outside_of_returns_the_count_the_bad_line_count_and_whether_an_init_event_was_seen(tmp_path):
    ev = [init("/r/work"), use("Write", "/x/p.md"), result()]
    assert score.outside_of(write(tmp_path, ev)) == (1, 0, True)
    assert score.outside_of(write(tmp_path, [init("/r/work"), "{broken", result()], pair=False)) == (0, 1, True)
    assert score.outside_of(write(tmp_path, [result()], pair=False)) == (0, 0, False)
    assert score.outside_of(write(tmp_path, [], pair=False)) == (0, 0, False)
    with pytest.raises(ValueError):
        score.outside_of(write(tmp_path, ["{broken"], pair=False))


@pytest.mark.parametrize("val,want", [(".\\./*", 1), ("\\.\\./*", 1), ("{\\.,}./*", 1), ("src/*.py", 0), ("a\\b", 1), ("*.{py,md}", 0)])
def test_a_backslash_in_a_pattern_counts_as_outside_because_an_escape_can_build_a_dotdot(tmp_path, val, want):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra), result()]) == want, (name, val)


@pytest.mark.parametrize("path,val,want", [("{..,x}", "*", 1), ("src/**", "../../*", 1), ("src", "*.py", 0), ("{a,b}", "*.py", 0), ("src/*", "../x", 1),
                                           ("src\\.", "*", 1), ("a", "**/*.py", 0), ("src", "../*.py", 0), ("/r/work/src", "../x/*", 0)])
def test_the_tools_path_input_is_tested_with_the_pattern_as_one_joined_pattern(tmp_path, path, val, want):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra, path=path), result()]) == want, (name, path, val)


@pytest.mark.parametrize("path,want", [("src", 0), ("src/sub", 0), ("{a,b}", 1), ("a\\b", 1), ("*/..", 1), ("a/[x]/../..", 1), ("a/../b", 0), ("src/*", 0)])
def test_a_path_alone_is_outside_when_it_has_a_brace_a_backslash_or_a_dotdot_after_a_glob_char(tmp_path, path, want):
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", path=path), result()]) == want, path
    assert outside(tmp_path, [init("/r/work"), use_in("Grep", path=path, pattern="x"), result()]) == want, path


def timed(fn):
    tracemalloc.start()
    start = time.perf_counter()
    try:
        out = fn()
        assert time.perf_counter() - start < 1.0
        assert tracemalloc.get_traced_memory()[1] < 20 * 1024 * 1024
    finally:
        tracemalloc.stop()
    return out


@pytest.mark.parametrize("val", ["{" + "a," * 100_000 + "b}/*", "{" + "a," * 10_000 + "b}" + "x" * 100_000, "{" * 10_000 + "a" + "}" * 10_000,
                                 "{a}" * 100_000, "../" * 50_000 + "x", "x/" * 50_000 + "*/" + "../" * 50_000, "a" * 600_000, "?/" * 2_100],
                         ids=["commas", "commas-suffix", "nesting", "groups", "dotdots", "glob-dotdots", "long-plain", "long-classes"])
def test_a_pathological_pattern_is_counted_outside_fast(tmp_path, val):
    from ace_playbook.lab import outside as out
    assert timed(lambda: out.inputs("/r/work", "Glob", {"pattern": val})) == 1
    assert timed(lambda: out.inputs("/r/work", "Grep", {"glob": val, "pattern": "x", "path": "src"})) == 1


def test_expand_bounds_the_top_level_commas_the_total_and_the_nesting(tmp_path):
    from ace_playbook.lab import outside as out
    assert sorted(out.expand("{a,{b,c}}x")) == ["ax", "bx", "cx"]
    assert len(out.expand("{" + ",".join(f"a{i}" for i in range(64)) + "}")) == 64
    for bad in ("{" + ",".join(f"a{i}" for i in range(65)) + "}", "{" * 33 + "a" + "}" * 33):
        with pytest.raises(ValueError):
            out.expand(bad)
    assert out.expand("{" * 32 + "a" + "}" * 32) == ["a"]


def test_outside_scan_also_returns_whether_the_stream_had_any_event_blank_lines_aside(tmp_path):
    assert score.outside_scan(write(tmp_path, [init("/r/work"), use("Write", "/x/p.md"), result()])) == (1, 0, True, True)
    assert score.outside_scan(write(tmp_path, [result()], pair=False)) == (0, 0, False, True)
    assert score.outside_scan(write(tmp_path, ["", "  "], pair=False)) == (0, 0, False, False)
    assert score.outside_scan(write(tmp_path, [], pair=False)) == (0, 0, False, False)
    with pytest.raises(ValueError):
        score.outside_scan(write(tmp_path, ["{broken"], pair=False))


@pytest.mark.parametrize("val,want", [(".?/*", 1), (".*/x", 1), ("..*/x", 1), (".*./x", 1), ("??/*", 0), ("?./*", 0), ("*.*", 0), ("*./x", 0), ("**/*.*", 0),
                                      ("src/*.*", 0), ("./*", 0), ("*.{py,md}", 0), ("src/**/*.py", 0), ("**/test_*.py", 0), ("{src,tests}/**/*.py", 0),
                                      ("src/*.py", 0), ("{.*,x}/*", 1), ("src/.?/.?/x.py", 1), ("src/.?/x.py", 0), ("*/.*/*", 1)])
def test_a_component_that_starts_with_a_dot_and_may_match_dotdot_counts_as_dotdot_but_a_leading_wildcard_never_does(tmp_path, val, want):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra), result()]) == want, (name, val)


def test_a_dotdot_wildcard_in_a_joined_path_counts(tmp_path):
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", pattern="*.py", path=".*"), result()]) == 1
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", pattern=".*/*", path="src"), result()]) == 0      # src/../* is /r/work/*
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", pattern=".*/.*/*", path="src"), result()]) == 1
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", pattern="*.*", path="??"), result()]) == 0


@pytest.mark.parametrize("n,want", [(4096, 0), (4097, 1)])
def test_a_pattern_over_4096_characters_counts_as_outside_before_it_is_expanded(tmp_path, n, want):
    val = "a" * (n - 2) + "/*"
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra), result()]) == want, (name, n)


@pytest.mark.parametrize("plen,want", [(4094, 0), (4095, 1)])
def test_a_joined_pattern_over_4096_characters_counts_as_outside(tmp_path, plen, want):
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", pattern="*", path="p" * plen), result()]) == want       # path + "/*"


@pytest.mark.parametrize("bad", [["a"], {"a": 1}, 7, True, 0, []], ids=["list", "dict", "int", "bool", "zero", "empty-list"])
def test_a_non_string_pattern_glob_or_path_input_counts_as_outside(tmp_path, bad):
    ev = lambda inp: outside(tmp_path, [init("/r/work"), use_in("Glob", **inp), result()])   # noqa: E731
    for key in ("pattern", "glob", "path", "file_path", "notebook_path"):
        assert ev({key: bad}) == 1, key
    assert ev({"pattern": "*.py", "path": None}) == 0


# ------------------------------------------------------------------ wave 8: the character whitelist replaces escape hunting
R4_ESCAPES = ["{Z..a}/etc/hosts", "{Z..a}.]./{Z..a}.]./*", "..{Z..a}/*", "{Z..a}/*", ".[^a]/*", ".[[:punct:]]/*", ".[[=.=]]/*", "[^a][^a]/*",
              "[.][.]/*", ".\\./*", "a\x00b/*", "\x00", "x ../../*"]
OTHER_CHARS = ["{1..3}/*", "{a,{1..3}}/*", "{..,x}/*", "[ab]/*.py", "src/[a-z]x.py", "[!/]x/*.py", "a[/", "a\\b", "a b/*", "a\tb", "a\nb", "a=b", "a:b",
               "a^b", "a~b", "~", "~/x", "a!b", "!!a", "a(b)", "a$b", "a+b", "a@b", "a#b", "a%b", "a;b", "a'b", 'a"b', "a|b", "a&b", "a`b",
               "caf\u00e9/*", "\uff0e\uff0e/*", "a\u2025/*", "\u202e*"]
PILOT_AND_COMMON = ["**/*.py", "**/config.json", "**/*", "**/*.{py,md,toml,cfg,txt}", "*.{py,md}", "src/**/*.py", "**/test_*.py", "{src,tests}/**/*.py",
                    "**/*.*", "src/*.*", "a-b/c_d.e/*", "{a,b}{c,d}/*"]


@pytest.mark.parametrize("val", R4_ESCAPES + OTHER_CHARS)
def test_any_character_outside_the_whitelist_and_any_dotdot_in_a_brace_body_counts_as_outside(tmp_path, val):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra), result()]) == 1, (name, val)
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra, path="src"), result()]) == 1, (name, val, "joined")


@pytest.mark.parametrize("path", ["**/[.][.]", "[.][.]", "a/[x]", "a b", "{Z..a}", "a\x00", "caf\u00e9", "a=b", "~", "a~"])
def test_a_path_alone_goes_through_the_same_character_rule(tmp_path, path):
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", path=path), result()]) == 1, path
    assert outside(tmp_path, [init("/r/work"), use_in("Grep", path=path, pattern="x"), result()]) == 1, path


@pytest.mark.parametrize("val", PILOT_AND_COMMON)
def test_every_pilot_and_common_pattern_is_inside_the_whitelist(tmp_path, val):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra), result()]) == 0, (name, val)
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra, path="src"), result()]) == 0, (name, val, "joined")


def test_a_grep_glob_may_start_with_one_bang_only_and_a_glob_pattern_never_may(tmp_path):
    ev = lambda name, key, val, **kw: outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **kw), result()])   # noqa: E731
    assert ev("Grep", "glob", "!.git/**", pattern="x") == 0 and ev("Grep", "glob", "!a", pattern="x") == 0
    assert ev("Grep", "glob", "!*.py", pattern="x", path="src") == 0
    assert ev("Grep", "glob", "!!a", pattern="x") == 1 and ev("Grep", "glob", "a!b", pattern="x") == 1 and ev("Grep", "glob", "a/!b", pattern="x") == 1
    assert ev("Grep", "glob", "!../../*", pattern="x") == 1 and ev("Grep", "glob", "!/etc/*", pattern="x") == 1
    assert ev("Glob", "pattern", "!.git/**") == 1 and ev("Glob", "pattern", "!*.py", path="src") == 1


def test_a_path_with_a_comma_or_inside_chars_stays_inside_and_a_brace_in_a_path_alone_stays_outside(tmp_path):
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", path="src/a,b"), result()]) == 0
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", path="/r/work/src-1/a_b.c"), result()]) == 0
    assert outside(tmp_path, [init("/r/work"), use_in("Glob", path="{a,b}"), result()]) == 1


@pytest.mark.parametrize("tail,want", [("*", 0), ("*/../y", 1)])
def test_a_64_alternative_pattern_under_4096_characters_is_expanded_in_bounded_time_and_memory(tmp_path, tail, want):
    from ace_playbook.lab import outside as out
    val = "{" + ",".join(f"d{i}" for i in range(64)) + "}/" + "x/" * 1500 + tail
    assert len(val) < 4096
    assert timed(lambda: out.inputs("/r/work", "Glob", {"pattern": val})) == want
    assert timed(lambda: out.inputs("/r/work", "Grep", {"glob": val, "pattern": "x", "path": "src"})) == want


# ------------------------------------------------------------------ wave 9, C-A: braces expand BEFORE the path join
@pytest.mark.parametrize("path,val,want", [("src", "{/etc/hosts,x}", 1), ("/r/work", "{/etc/hosts,x}", 1), ("src", "{,}/etc/*", 1), ("src", "{~/x,y}", 1),
                                           ("src", "{a,b}/*.py", 0), ("src", "*.{py,md}", 0), ("/r/work/src", "{/r/work/a,b}", 0),
                                           ("src", "{x,/r/work/a}", 0), ("src", "{x,/r/workshop/a}", 1), ("src", "{x,../../y}", 1),
                                           ("src", "{x,y}/../z", 0), ("{a,b}", "{c,/etc/*}", 1), ("{a,b}", "{c,d}/*.py", 0)])
def test_an_absolute_alternative_of_a_brace_group_is_tested_as_a_path_and_never_joined_to_the_tools_path(tmp_path, path, val, want):
    for name, key in (("Glob", "pattern"), ("Grep", "glob")):
        extra = {} if name == "Glob" else {"pattern": "x"}
        assert outside(tmp_path, [init("/r/work"), use_in(name, **{key: val}, **extra, path=path), result()]) == want, (name, path, val)


def test_a_brace_alternative_outside_is_outside_without_a_path_too_and_the_cap_covers_the_path_times_the_pattern():
    from ace_playbook.lab import outside as out
    assert out.inputs("/r/work", "Glob", {"pattern": "{/etc/hosts,x}"}) == 1 and out.inputs("/r/work", "Glob", {"pattern": "{x,y}"}) == 0
    wide = "{" + ",".join(f"d{i}" for i in range(33)) + "}/*"
    assert out.inputs("/r/work", "Glob", {"pattern": wide, "path": "{a,b}"}) == 1                    # 33 x 2 alternatives: over the cap, fail closed
    assert out.inputs("/r/work", "Glob", {"pattern": wide, "path": "src"}) == 0
