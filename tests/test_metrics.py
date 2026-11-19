"""Unit tests for pure metrics calculation, breed selection, and color/bar helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from stats_gen.cats import select_roaming_breeds  # noqa: E402
from stats_gen.config import load_breeds, load_config  # noqa: E402
from stats_gen.metrics import (  # noqa: E402
    aggregate_languages,
    allocate_bar_widths,
    compute_streaks,
    format_compact,
    normalize_lang_color,
)


class TestMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_config(ROOT / "config" / "profile.json")
        cls.breeds = load_breeds(ROOT / "config" / "breeds.json")

    def test_streaks_active_today(self) -> None:
        days = [
            {"date": "2026-11-15", "contributionCount": 0},
            {"date": "2026-11-16", "contributionCount": 2},
            {"date": "2026-11-17", "contributionCount": 4},
            {"date": "2026-11-18", "contributionCount": 1},
        ]
        self.assertEqual(compute_streaks(days), (3, 3))

    def test_streaks_today_zero_yesterday_active(self) -> None:
        days = [
            {"date": "2026-11-15", "contributionCount": 0},
            {"date": "2026-11-16", "contributionCount": 3},
            {"date": "2026-11-17", "contributionCount": 5},
            {"date": "2026-11-18", "contributionCount": 0},
        ]
        self.assertEqual(compute_streaks(days), (2, 2))

    def test_streaks_broken_gap_preserves_longest(self) -> None:
        days = [
            {"date": "2026-11-13", "contributionCount": 1},
            {"date": "2026-11-14", "contributionCount": 2},
            {"date": "2026-11-15", "contributionCount": 3},
            {"date": "2026-11-16", "contributionCount": 1},
            {"date": "2026-11-17", "contributionCount": 0},
            {"date": "2026-11-18", "contributionCount": 0},
        ]
        self.assertEqual(compute_streaks(days), (0, 4))

    def test_streaks_empty(self) -> None:
        self.assertEqual(compute_streaks([]), (0, 0))

    def test_language_aggregation_exclusions_and_color_lift(self) -> None:
        repos = [
            {
                "languages": {
                    "edges": [
                        {"size": 5000, "node": {"name": "Python", "color": "#3572A5"}},
                        {"size": 9000, "node": {"name": "Jupyter Notebook", "color": "#DA5B0B"}},
                        {"size": 3000, "node": {"name": "C", "color": "#555555"}},
                        {"size": 2000, "node": {"name": "ObscureDark", "color": "#010101"}},
                    ]
                }
            }
        ]
        norm = self.cfg["color_norm"]
        default_color = self.cfg["theme"]["default_lang_color"]
        ranked = aggregate_languages(repos, {"Jupyter Notebook"}, norm, default_color, limit=2, pct_scale=100.0)
        self.assertEqual([item["name"] for item in ranked], ["Python", "C"])
        self.assertEqual(ranked[1]["color"], "#8b9bb4")
        lifted = normalize_lang_color("#010101", norm, default_color)
        self.assertNotEqual(lifted, "#010101")
        self.assertEqual(len(lifted), 7)

    def test_allocate_bar_widths_exact_sum_and_floor(self) -> None:
        langs = [
            {"name": "Python", "size": 99000},
            {"name": "TypeScript", "size": 500},
            {"name": "HLSL", "size": 10},
        ]
        widths = allocate_bar_widths(langs, total_width=332.0, min_width=2.0)
        self.assertEqual(len(widths), 3)
        self.assertAlmostEqual(sum(widths), 332.0, places=6)
        self.assertGreaterEqual(widths[1], 2.0)
        self.assertGreaterEqual(widths[2], 2.0)

        single = allocate_bar_widths([{"name": "Python", "size": 100}], total_width=332.0, min_width=2.0)
        self.assertEqual(single, [332.0])

    def test_format_compact(self) -> None:
        fmt = self.cfg["formatting"]
        self.assertEqual(format_compact(999, fmt), "999")
        self.assertEqual(format_compact(6888, fmt), "6.9k")
        self.assertEqual(format_compact(125000, fmt), "125k")

    def test_select_roaming_breeds_deterministic_and_excludes_pinned(self) -> None:
        a = select_roaming_breeds(self.breeds, 4, "2026-11-19")
        b = select_roaming_breeds(self.breeds, 4, "2026-11-19")
        self.assertEqual(a, b)
        self.assertEqual(len(set(a)), 4)
        self.assertNotIn("white", a)
        self.assertNotIn("black", a)

        different_seen = any(
            select_roaming_breeds(self.breeds, 4, f"2026-11-{day:02d}") != a
            for day in range(20, 26)
        )
        self.assertTrue(different_seen)


if __name__ == "__main__":
    unittest.main()
