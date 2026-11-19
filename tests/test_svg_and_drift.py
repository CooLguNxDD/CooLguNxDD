"""SVG generation, XML/XSS sanitization, secret redaction, and drift checks."""

from __future__ import annotations

import copy
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from stats_gen.cats import select_daily_poses, select_roaming_breeds  # noqa: E402
from stats_gen.config import apply_theme_preset, load_breeds, load_config, load_text_bundle, load_themes  # noqa: E402
from stats_gen.github_io import redact_secret  # noqa: E402
from stats_gen.svg import (  # noqa: E402
    CARD_RENDERERS,
    render_ai_tools_cat_svg,
    render_cat_strip_svg,
    render_catportfolio_svg,
    render_hero_svg,
    sanitize_color,
)

SEED_RE = re.compile(r"<!-- seed: ([A-Za-z0-9_.:-]+) -->")
THEME_RE = re.compile(r"<!-- theme: ([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+) -->")
BREED_RE = re.compile(r"<!-- breed: ([A-Za-z0-9_-]+) -->")


class TestSvgAndDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_config(ROOT / "config" / "profile.json")
        cls.breeds = load_breeds(ROOT / "config" / "breeds.json")
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

    def test_hero_aquarium_white_cat_and_reduced_motion(self) -> None:
        hero_svg = render_hero_svg(self.cfg, self.breeds, self.bundle["templates"])
        root_el = ET.fromstring(hero_svg)
        self.assertIsNone(root_el.find(".//{http://www.w3.org/2000/svg}script"))
        self.assertIn("@keyframes", hero_svg)
        self.assertIn("prefers-reduced-motion: reduce", hero_svg)
        self.assertIn(self.breeds["breeds"]["white"]["coat"], hero_svg)
        self.assertIn("cat-pupils", hero_svg)
        self.assertIn("cat-head", hero_svg)
        self.assertNotIn("cat-peek", hero_svg)
        self.assertIn("Agentic AI Platforms", hero_svg)
        self.assertNotIn("Production MCP platforms", hero_svg)
        for fish in self.cfg["hero"]["aquarium"]["fish"]:
            self.assertIn(fish["body_color"], hero_svg)

    def test_catportfolio_black_cat_strip_and_ai_tools_cat(self) -> None:
        cp_svg = render_catportfolio_svg(self.cfg, self.breeds, self.bundle["templates"])
        ET.fromstring(cp_svg)
        self.assertNotIn("<script", cp_svg.lower())
        self.assertIn("@keyframes", cp_svg)
        self.assertIn("prefers-reduced-motion: reduce", cp_svg)
        self.assertIn(self.breeds["breeds"]["black"]["coat"], cp_svg)
        self.assertIn(self.breeds["breeds"]["black"]["coat_rim"], cp_svg)
        self.assertIn("cat-pupils", cp_svg)
        self.assertIn("cat-head", cp_svg)

        ai_cat_svg = render_ai_tools_cat_svg(self.cfg, self.breeds, self.bundle["templates"])
        ET.fromstring(ai_cat_svg)
        self.assertNotIn("<script", ai_cat_svg.lower())
        self.assertIn("@keyframes", ai_cat_svg)
        self.assertIn("prefers-reduced-motion: reduce", ai_cat_svg)
        self.assertIn("catch-fish", ai_cat_svg)
        self.assertIn("cat-pupils", ai_cat_svg)
        self.assertIn("cat-head", ai_cat_svg)
        self.assertIn(self.breeds["breeds"]["ginger"]["coat"], ai_cat_svg)

        roaming = select_roaming_breeds(self.breeds, 3, "2026-11-19")
        strip_svg = render_cat_strip_svg(
            self.cfg, self.breeds, self.bundle["templates"],
            roaming_ids=roaming, seed="2026-11-19",
        )
        ET.fromstring(strip_svg)
        self.assertNotIn("<script", strip_svg.lower())
        self.assertIn("@keyframes", strip_svg)
        self.assertIn("prefers-reduced-motion: reduce", strip_svg)
        for bid in roaming:
            self.assertIn(self.breeds["breeds"][bid]["label"], strip_svg)

    def test_all_renderers_valid_xml_no_scripts(self) -> None:
        roaming = select_roaming_breeds(self.breeds, 3, "2026-11-19")
        ctx = {
            "cfg": self.cfg,
            "breeds": self.breeds,
            "templates": self.bundle["templates"],
            "metrics": self.sample_metrics,
            "seed": "2026-11-19",
            "strip_breed_ids": roaming,
            "include_private": False,
            "has_exclusions": True,
            "updated_date": "2026-11-19",
        }
        for key in ("hero", "catportfolio", "cat_strip", "ai_tools_cat", "stats", "top_langs"):
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

    def test_generated_cat_svgs_no_drift(self) -> None:
        themes = load_themes(ROOT / "config" / "themes.json")

        def cfg_for(svg_text: str) -> dict:
            m = THEME_RE.search(svg_text)
            self.assertIsNotNone(m)
            return apply_theme_preset(self.cfg, themes, theme_id=m.group(1), accent_id=m.group(2))

        def breed_of(svg_text: str) -> str:
            m = BREED_RE.search(svg_text)
            self.assertIsNotNone(m)
            return m.group(1)

        hero_disk = (ROOT / "assets" / "hero-banner.svg").read_text(encoding="utf-8")
        self.assertEqual(hero_disk, render_hero_svg(cfg_for(hero_disk), self.breeds, self.bundle["templates"], breed_id=breed_of(hero_disk)))

        cp_disk = (ROOT / "assets" / "catportfolio-card.svg").read_text(encoding="utf-8")
        self.assertEqual(cp_disk, render_catportfolio_svg(cfg_for(cp_disk), self.breeds, self.bundle["templates"], breed_id=breed_of(cp_disk)))

        ai_disk = (ROOT / "assets" / "ai-tools-cat.svg").read_text(encoding="utf-8")
        self.assertEqual(ai_disk, render_ai_tools_cat_svg(cfg_for(ai_disk), self.breeds, self.bundle["templates"], breed_id=breed_of(ai_disk)))

        strip_disk = (ROOT / "assets" / "cat-fleet-strip.svg").read_text(encoding="utf-8")
        strip_match = SEED_RE.search(strip_disk)
        self.assertIsNotNone(strip_match)
        strip_seed = strip_match.group(1)
        strip_roaming = select_roaming_breeds(self.breeds, 3, strip_seed)
        daily_roaming = select_roaming_breeds(self.breeds, 6, strip_seed, include_pinned=True)[3:]
        strip_cfg = cfg_for(strip_disk)
        daily_poses = select_daily_poses(["stroll", "nap", "play"], 3, strip_seed)
        candidates = (
            render_cat_strip_svg(strip_cfg, self.breeds, self.bundle["templates"], roaming_ids=strip_roaming, seed=strip_seed),
            render_cat_strip_svg(strip_cfg, self.breeds, self.bundle["templates"], roaming_ids=daily_roaming, seed=strip_seed, poses=daily_poses),
        )
        self.assertIn(strip_disk, candidates)

    def test_on_disk_generated_svgs_valid_xml_no_bom(self) -> None:
        themes = load_themes(ROOT / "config" / "themes.json")
        latte_bg = themes["themes"]["latte"]["bg_start"]
        light_cfg = apply_theme_preset(self.cfg, themes, theme_id="latte", accent_id="latte_mauve")
        names = (
            "hero-banner.svg",
            "catportfolio-card.svg",
            "cat-fleet-strip.svg",
            "ai-tools-cat.svg",
            "github-stats.svg",
            "top-langs.svg",
        )
        for name in names:
            raw = (ROOT / "assets" / name).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in {name}")
            ET.fromstring(raw.decode("utf-8"))

            light_raw = (ROOT / "assets" / "light" / name).read_bytes()
            self.assertFalse(light_raw.startswith(b"\xef\xbb\xbf"), f"UTF-8 BOM in light/{name}")
            light_text = light_raw.decode("utf-8")
            ET.fromstring(light_text)
            self.assertIn(latte_bg, light_text)

        hero_light = (ROOT / "assets" / "light" / "hero-banner.svg").read_text(encoding="utf-8")
        hero_breed = BREED_RE.search(hero_light)
        self.assertIsNotNone(hero_breed)
        self.assertEqual(hero_light, render_hero_svg(light_cfg, self.breeds, self.bundle["templates"], breed_id=hero_breed.group(1)))

        cp_light = (ROOT / "assets" / "light" / "catportfolio-card.svg").read_text(encoding="utf-8")
        cp_breed = BREED_RE.search(cp_light)
        self.assertIsNotNone(cp_breed)
        self.assertEqual(cp_light, render_catportfolio_svg(light_cfg, self.breeds, self.bundle["templates"], breed_id=cp_breed.group(1)))
        ai_light = (ROOT / "assets" / "light" / "ai-tools-cat.svg").read_text(encoding="utf-8")
        ai_breed = BREED_RE.search(ai_light)
        self.assertIsNotNone(ai_breed)
        self.assertEqual(ai_light, render_ai_tools_cat_svg(light_cfg, self.breeds, self.bundle["templates"], breed_id=ai_breed.group(1)))


if __name__ == "__main__":
    unittest.main()
