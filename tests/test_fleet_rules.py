"""Deterministic CI gates backing Cat Fleet v2 rules (3, 4, 6, 7, 14)."""

from __future__ import annotations

import ast
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from stats_gen.config import apply_theme_preset, load_breeds, load_config, load_themes  # noqa: E402

MAX_FUNC_LINES = 60
MAX_IF_ARMS = 3
MAX_NEST_DEPTH = 2
MAX_MATCH_CASES = 3
FORBIDDEN_PURE_IMPORTS = {"github_io", "urllib", "http", "socket", "os", "datetime"}
FORBIDDEN_CATS_IMPORTS = FORBIDDEN_PURE_IMPORTS | {"svg"}


def _is_elif(node: ast.If) -> bool:
    return len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)


def _if_arms(node: ast.If) -> int:
    n = 1
    while _is_elif(node):
        node, n = node.orelse[0], n + 1
    return n


def _ifexp_arms(node: ast.IfExp) -> int:
    n = 2
    while isinstance(node.orelse, ast.IfExp):
        node, n = node.orelse, n + 1
    return n


def _nest_depth(node: ast.If) -> int:
    if not node.orelse:
        return 0
    body = node.body + ([] if _is_elif(node) else node.orelse)
    inner = [c for stmt in body for c in ast.walk(stmt) if isinstance(c, ast.If) and c is not node]
    return 1 + max((_nest_depth(c) for c in inner), default=0)


def _subject(node: ast.stmt) -> str:
    if not isinstance(node, ast.If):
        return ""
    test = node.test.values[0] if isinstance(node.test, ast.BoolOp) else node.test
    is_cmp = isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Eq, ast.Is, ast.In))
    return ast.unparse(test.left) if is_cmp else ""


def _check_ast_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    findings: list[str] = []
    fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in fns:
        length = (fn.end_lineno or fn.lineno) - fn.lineno + 1
        if length > MAX_FUNC_LINES:
            findings.append(f"{path.name}:{fn.lineno} {fn.name} is {length} lines")
        for node in ast.walk(fn):
            if isinstance(node, ast.If):
                if _if_arms(node) >= MAX_IF_ARMS:
                    findings.append(f"{path.name}:{node.lineno} {fn.name} has if/elif ladder")
                if _nest_depth(node) >= MAX_NEST_DEPTH:
                    findings.append(f"{path.name}:{node.lineno} {fn.name} has deep if/else nesting")
            if isinstance(node, ast.IfExp) and _ifexp_arms(node) >= MAX_IF_ARMS:
                findings.append(f"{path.name}:{node.lineno} {fn.name} has nested ternary")
            if isinstance(node, ast.Match) and len(node.cases) >= MAX_MATCH_CASES:
                findings.append(f"{path.name}:{node.lineno} {fn.name} has match ladder")
        for block_owner in ast.walk(fn):
            for field in ("body", "orelse"):
                block = getattr(block_owner, field, None)
                if isinstance(block, list):
                    subjects = [_subject(s) for s in block]
                    for subj in {x for x in subjects if x}:
                        if subjects.count(subj) >= MAX_IF_ARMS:
                            findings.append(f"{path.name}:{fn.lineno} {fn.name} has sibling ifs over {subj}")
    return findings


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.lstrip(".").split(".")[0])
    return mods


class TestFleetRules(unittest.TestCase):
    def test_no_ladders_or_long_functions_in_scripts(self) -> None:
        all_findings: list[str] = []
        for py_file in sorted((ROOT / "scripts").rglob("*.py")):
            all_findings.extend(_check_ast_file(py_file))
        self.assertEqual(all_findings, [])

    def test_pure_core_import_boundaries(self) -> None:
        for rel in ("scripts/stats_gen/metrics.py", "scripts/stats_gen/svg.py"):
            imported = _imported_roots(ROOT / rel)
            overlap = imported & FORBIDDEN_PURE_IMPORTS
            self.assertEqual(overlap, set(), f"{rel} imported forbidden modules: {overlap}")

        cats_imports = _imported_roots(ROOT / "scripts/stats_gen/cats.py")
        self.assertEqual(cats_imports & FORBIDDEN_CATS_IMPORTS, set())

    def test_config_and_breeds_contract_validation(self) -> None:
        cfg = load_config(ROOT / "config" / "profile.json")
        self.assertEqual(cfg["username"], "CooLguNxDD")
        themes = load_themes(ROOT / "config" / "themes.json")
        for tid in ("catportfolio", "mocha", "macchiato", "frappe", "latte"):
            self.assertIn(tid, themes["themes"])
        themed = apply_theme_preset(cfg, themes, theme_id="mocha", accent_id="mauve")
        self.assertEqual(themed["theme"]["bg_start"], themes["themes"]["mocha"]["bg_start"])
        self.assertEqual(themed["theme"]["accent_start"], themes["accents"]["mauve"]["accent_start"])
        breeds = load_breeds(ROOT / "config" / "breeds.json")
        self.assertIn("white", breeds["breeds"])
        self.assertIn("black", breeds["breeds"])

        bad_cfg = copy.deepcopy(cfg)
        bad_cfg["theme"]["bg_start"] = "invalid-hex"
        bad_breeds = copy.deepcopy(breeds)
        bad_breeds["breeds"]["white"]["coat"] = "bad-color"
        with tempfile.TemporaryDirectory() as tmp:
            bad_cfg_path = Path(tmp) / "bad.json"
            bad_cfg_path.write_text(json.dumps(bad_cfg), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(bad_cfg_path)

            bad_breeds_path = Path(tmp) / "bad_breeds.json"
            bad_breeds_path.write_text(json.dumps(bad_breeds), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_breeds(bad_breeds_path)

    def test_docs_and_readme_links(self) -> None:
        self.assertTrue((ROOT / ".fleet-rules.md").is_file())
        self.assertIn("@.fleet-rules.md", (ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn(".fleet-rules.md", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Agentic AI platforms", readme)
        self.assertIn("Rust", readme)
        self.assertIn("./config/themes.json", readme)
        for forbidden in ("vercel.app", "github-stats-extended", "github-readme-stats", "production MCP platforms"):
            self.assertNotIn(forbidden, readme)
        for rel in re.findall(r"\./[A-Za-z0-9_./-]+", readme):
            self.assertTrue((ROOT / rel[2:]).exists(), f"Missing path referenced in README.md: {rel}")


if __name__ == "__main__":
    unittest.main()
