"""Declarative configuration and template loading with contract validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TEMPLATE_SPLITTER = "<!-- BODY -->"

REQUIRED_SECTIONS = (
    "username",
    "github",
    "theme",
    "formatting",
    "color_norm",
    "excluded_languages",
    "hero",
    "catportfolio_card",
    "cat_strip",
    "stats_card",
    "langs_card",
    "messages",
    "svg_snippets",
)

BREED_HEX_KEYS = (
    "coat",
    "coat_shadow",
    "coat_rim",
    "chest",
    "inner_ear",
    "eye",
    "pupil",
    "nose",
    "mouth",
    "blush",
    "whisker",
    "accent",
    "accent2",
)

REQUIRED_BREED_POSES = ("sit", "peek", "stroll", "nap", "play")
REQUIRED_BREED_PATTERNS = ("solid", "tabby", "calico", "tuxedo", "pointed", "nebula")
MIN_ROAMING_BREEDS = 4

THEME_HEX_KEYS = (
    "bg_start",
    "bg_end",
    "border",
    "accent_start",
    "accent_end",
    "text_primary",
    "text_secondary",
    "text_muted",
    "pill_lavender",
    "pill_cyan",
    "chip_bg",
    "chip_border",
    "icon_cyan",
    "icon_violet",
    "default_lang_color",
)

POSITIVE_NUMBER_PATHS = (
    ("github", "timeout_sec"),
    ("github", "repos_page_size"),
    ("github", "langs_per_repo"),
    ("theme", "card_radius"),
    ("theme", "accent_width"),
    ("formatting", "year_days"),
    ("formatting", "compact_thousand"),
    ("formatting", "pct_scale"),
    ("hero", "width"),
    ("hero", "height"),
    ("catportfolio_card", "width"),
    ("catportfolio_card", "height"),
    ("cat_strip", "width"),
    ("cat_strip", "height"),
    ("stats_card", "width"),
    ("stats_card", "height"),
    ("langs_card", "width"),
    ("langs_card", "height"),
    ("langs_card", "limit"),
)


def _validate_sections(cfg: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_SECTIONS if k not in cfg]
    if missing:
        raise ValueError(f"config missing keys: {missing}")
    if not isinstance(cfg["username"], str) or not cfg["username"].strip():
        raise ValueError("config username invalid")


def _validate_theme(theme: dict[str, Any]) -> None:
    for key in THEME_HEX_KEYS:
        val = theme.get(key)
        if not isinstance(val, str) or not HEX_COLOR_RE.match(val):
            raise ValueError(f"theme.{key} invalid hex: {val!r}")


def _validate_numbers(cfg: dict[str, Any]) -> None:
    for section, key in POSITIVE_NUMBER_PATHS:
        val = cfg[section].get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool) or val <= 0:
            raise ValueError(f"{section}.{key} must be > 0")
    if float(cfg["langs_card"]["bar"]["width"]) <= 0:
        raise ValueError("langs_card.bar.width must be > 0")
    if float(cfg["stats_card"]["ring"]["r"]) <= 0:
        raise ValueError("stats_card.ring.r must be > 0")


def _validate_shapes(cfg: dict[str, Any]) -> None:
    non_empty = (
        cfg["hero"].get("chips"),
        cfg["hero"].get("aquarium", {}).get("fish"),
        cfg["catportfolio_card"].get("chips"),
        cfg["cat_strip"].get("slots"),
        cfg["stats_card"].get("rows"),
        cfg["langs_card"].get("legend", {}).get("cols"),
    )
    if any(not isinstance(item, list) or not item for item in non_empty):
        raise ValueError("card arrays must be non-empty lists")
    if not isinstance(cfg["messages"], dict) or not isinstance(cfg["svg_snippets"], dict):
        raise ValueError("messages and svg_snippets must be objects")


def load_config(path: Path) -> dict[str, Any]:
    """Load profile.json and enforce schema contracts before rendering."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config root must be object")
    _validate_sections(raw)
    _validate_theme(raw["theme"])
    _validate_numbers(raw)
    _validate_shapes(raw)
    return raw


def _validate_breed_entry(breed_id: str, spec: dict[str, Any], patterns: set[str]) -> None:
    if not isinstance(spec, dict) or spec.get("pattern") not in patterns:
        raise ValueError(f"breed.{breed_id} invalid pattern")
    for key in BREED_HEX_KEYS:
        val = spec.get(key)
        if not isinstance(val, str) or not HEX_COLOR_RE.match(val):
            raise ValueError(f"breed.{breed_id}.{key} invalid hex: {val!r}")


def load_breeds(path: Path) -> dict[str, Any]:
    """Load breeds.json and verify pinned breeds, roaming pool, and poses."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("breeds"), dict):
        raise ValueError("breeds.json missing breeds object")
    markings, poses = raw.get("markings"), raw.get("poses")
    if not isinstance(markings, dict) or not isinstance(poses, dict):
        raise ValueError("breeds.json missing markings or poses")
    if any(p not in markings for p in REQUIRED_BREED_PATTERNS):
        raise ValueError("breeds.json missing pattern template")
    if any(p not in poses for p in REQUIRED_BREED_POSES):
        raise ValueError("breeds.json missing pose template")
    breeds = raw["breeds"]
    for pinned in ("white", "black"):
        if pinned not in breeds:
            raise ValueError(f"breeds.json missing pinned breed: {pinned}")
    for bid, spec in breeds.items():
        _validate_breed_entry(bid, spec, set(markings.keys()))
    roaming = [bid for bid, spec in breeds.items() if spec.get("roaming")]
    if len(roaming) < MIN_ROAMING_BREEDS:
        raise ValueError("breeds.json needs at least 4 roaming breeds")
    return raw


def _split_template(raw: str) -> dict[str, str]:
    if TEMPLATE_SPLITTER not in raw:
        return {"defs": "", "body": raw}
    defs_part, body_part = raw.split(TEMPLATE_SPLITTER, 1)
    return {"defs": defs_part.rstrip("\n"), "body": body_part.lstrip("\n")}


def load_text_bundle(root: Path) -> dict[str, dict[str, Any]]:
    """Load declarative GraphQL queries and SVG templates from root."""
    base = Path(root)
    queries = {
        p.name.split(".")[0]: p.read_text(encoding="utf-8")
        for p in sorted((base / "queries").iterdir())
        if p.is_file()
    }
    templates = {
        p.name.split(".")[0]: _split_template(p.read_text(encoding="utf-8"))
        for p in sorted((base / "templates").iterdir())
        if p.is_file()
    }
    return {"queries": queries, "templates": templates}
