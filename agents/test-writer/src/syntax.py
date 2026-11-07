"""Pure: syntax checks without running anything. Python is compiled (never executed); JS/TS and Go get a
bracket/string balance check and the shape of a test."""

from __future__ import annotations

import re
import textwrap

import scan

_PAIRS = {")": "(", "]": "[", "}": "{"}
_JS_TEST = re.compile(r"\b(?:it|test)(?:\.each\s*\()?")
_JS_CALLBACK = re.compile(r"=>|\bfunction\b")
_GO_TEST = re.compile(r"\bfunc\s+Test\w*\s*\(\s*t\s+\*testing\.T\s*\)")


def python_ok(code: str) -> str:
    """"" when `code` compiles, else the error. Compiling never runs the code."""
    try:
        compile(textwrap.dedent(code), "<test>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as e:
        return f"syntax error: {getattr(e, 'msg', str(e))} (line {getattr(e, 'lineno', '?')})"
    return ""


def balanced(code: str) -> str:
    stack: list[str] = []
    i = 0
    while i < len(code):
        nxt = scan.skip_string(code, i)
        if nxt != i:
            i = nxt
            continue
        ch = code[i]
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}" and (not stack or stack.pop() != _PAIRS[ch]):
            return f"unbalanced '{ch}'"
        i += 1
    return f"unclosed '{stack[-1]}'" if stack else ""


def js_ok(code: str) -> str:
    if "```" in code:
        return "stray code fence"
    problem = balanced(code)
    if problem:
        return f"syntax error: {problem}"
    if not _JS_TEST.search(code) or not _JS_CALLBACK.search(code):
        return "not an it()/test() block with a callback"
    if "expect(" not in code:
        return "no expect() assertion"
    return ""


def go_ok(code: str) -> str:
    if "```" in code:
        return "stray code fence"
    problem = balanced(code)
    if problem:
        return f"syntax error: {problem}"
    if not _GO_TEST.search(code):
        return "not a func TestXxx(t *testing.T)"
    if re.search(r"t\.Run\(\s*\"\"", code):
        return "unnamed t.Run subtest"
    return ""


def check(code: str, family: str) -> str:
    return {"python": python_ok, "js": js_ok, "go": go_ok}[family](code)
