"""SVG generation, XML/XSS sanitization, secret redaction, and drift checks."""

from __future__ import annotations

import copy
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from stats_gen.config import load_config, load_text_bundle  # noqa: E402
from stats_gen.github_io import redact_secret  # noqa: E402
from stats_gen.svg import CARD_RENDERERS, render_hero_svg, sanitize_color  # noqa: E402


class TestSvgAndDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_config(ROOT / "config" / "profile.json")
        cls.bundle = load_text_bundle(ROOT)
        cls.sample_metrics = {
            "contribs_12m": 6889,
            "restricted_12m": 5142,
            "all_time_contribs": 8818,
            "prs_total": 465,
            "active_days": 207,
            "current_streak": 57,
            "longest_streak": 57,
            "weekly_totals": [10, 20, 0, 40, 50, 35, 80, 110, 95, 75, 140, 60],
            "repos_count": 20,
            "top_langs": [
                {"name": "Python", "size": 500, "pct": 50.0, "color": "#3572a5"},
                {"name": "TypeScript", "size": 300, "pct": 30.0, "color": "#3178c6"},
                {"name": "C#", "size": 200, "pct": 20.0, "color": "#7355dd"},
            ],
        }

    def test_hero_aquarium_animations_and_reduced_motion(self) -> None:
        hero_svg = render_hero_svg(self.cfg, self.bundle["templates"])
        root_el = ET.fromstring(hero_svg)
        self.assertIsNone(root_el.find(".//{http://www.w3.org/2000/svg}script"))
        self.assertIn("@keyframes", hero_svg)
        self.assertIn("prefers-reduced-motion: reduce", hero_svg)
        for fish in self.cfg["hero"]["aquarium"]["fish"]:
            self.assertIn(fish["body_color"], hero_svg)
        self.assertIn("cat-tail", hero_svg)
        self.assertIn("cat-blink", hero_svg)

    def test_stats_and_langs_render_valid_xml_no_scripts(self) -> None:
        ctx = {
            "cfg": self.cfg,
            "templates": self.bundle["templates"],
            "metrics": self.sample_metrics,
            "include_private": False,
            "has_exclusions": True,
            "updated_date": "2026-11-19",
        }
        for key in ("hero", "stats", "top_langs"):
            svg_text = CARD_RENDERERS[key](ctx)
            tree = ET.fromstring(svg_text)
            self.assertIsNone(tree.find(".//{http://www.w3.org/2000/svg}script"))
            self.assertNotIn("<script", svg_text.lower())

    def test_sanitization_and_secret_redaction(self) -> None:
        hostile_metrics = copy.deepcopy(self.sample_metrics)
        hostile_metrics["top_langs"] = [
            {"name": "<script>alert(1)</script>&\"'", "size": 100, "pct": 100.0, "color": "url(javascript:1)"}
        ]
        ctx = {
            "cfg": self.cfg,
            "templates": self.bundle["templates"],
            "metrics": hostile_metrics,
            "include_private": False,
            "has_exclusions": False,
            "updated_date": "2026-11-19",
        }
        svg_text = CARD_RENDERERS["top_langs"](ctx)
        tree = ET.fromstring(svg_text)
        self.assertIsNone(tree.find(".//{http://www.w3.org/2000/svg}script"))
        self.assertIn("&lt;script&gt;", svg_text)
        self.assertEqual(sanitize_color("not-a-color", "#863bff"), "#863bff")
        self.assertEqual(redact_secret("failed bearer ghp_secret123 here", "ghp_secret123"), "failed bearer [REDACTED] here")

    def test_hero_banner_no_drift(self) -> None:
        on_disk = (ROOT / "assets" / "hero-banner.svg").read_text(encoding="utf-8")
        rendered = render_hero_svg(self.cfg, self.bundle["templates"])
        self.assertEqual(on_disk, rendered)

    def test_on_disk_generated_svgs_valid_xml_no_bom(self) -> None:
        for name in ("hero-banner.svg", "github-stats.svg", "top-langs.svg"):
            raw = (ROOT / "assets" / name).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {name}")
            ET.fromstring(raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
