from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_REPO_ROOT = Path(__file__).resolve().parents[3]

_TARGET_FILES = [
    "ai_trading_bot/core/diagnostics.py",
    "ai_trading_bot/core/analysis/attribution/rl_candidate_bench.py",
    "ai_trading_bot/gui/gui.py",
    "ai_trading_bot/research/sweep.py",
    "ai_trading_bot/research/ui.py",
    "scripts/agy_lauf.py",
    "scripts/lebenszeichen.py",
]


def _is_subprocess_call(node: ast.Call, rel_path: str) -> bool:
    if isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name) and node.func.value.id in {
            "subprocess",
            "asyncio",
        }:
            return node.func.attr in {
                "run",
                "Popen",
                "check_output",
                "check_call",
                "call",
                "create_subprocess_exec",
                "create_subprocess_shell",
            }
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "run"
        and rel_path.endswith("lebenszeichen.py")
    ):
        return True
    return False


def _vars_carrying_create_no_window(func_node: ast.AST) -> set[str]:
    """Return variable names assigned or subscript-assigned with CREATE_NO_WINDOW inside func_node."""
    vars_with_flag: set[str] = set()
    for stmt in ast.walk(func_node):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            val = stmt.value
            if val is None or "CREATE_NO_WINDOW" not in ast.unparse(val):
                continue
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for tgt in targets:
                if isinstance(tgt, ast.Name):
                    vars_with_flag.add(tgt.id)
                elif isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                    vars_with_flag.add(tgt.value.id)
    return vars_with_flag


@pytest.mark.vc0
@pytest.mark.parametrize("rel_path", _TARGET_FILES)
def test_windows_subprocess_spawns_include_create_no_window(rel_path: str) -> None:
    """#4667: Under pythonw.exe (no attached console) or interactive Windows runner tasks,
    any subprocess/create_subprocess_exec call without CREATE_NO_WINDOW allocates a
    visible conhost.exe console window on Windows."""
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel_path)

    assert (
        "CREATE_NO_WINDOW" in source
    ), f"{rel_path} spawns subprocesses but does not reference CREATE_NO_WINDOW"

    matched_calls = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        valid_kwarg_vars = _vars_carrying_create_no_window(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or not _is_subprocess_call(
                node, rel_path
            ):
                continue
            matched_calls += 1
            has_direct_flag = any(
                kw.arg == "creationflags"
                and "CREATE_NO_WINDOW" in ast.unparse(kw.value)
                for kw in node.keywords
            )
            has_verified_unpack = any(
                kw.arg is None
                and isinstance(kw.value, ast.Name)
                and kw.value.id in valid_kwarg_vars
                for kw in node.keywords
            )
            assert has_direct_flag or has_verified_unpack, (
                f"{rel_path}:{node.lineno} calls subprocess without "
                f"creationflags or verified **kwargs carrying CREATE_NO_WINDOW"
            )
    assert matched_calls >= 1, f"Expected at least 1 subprocess call in {rel_path}"
