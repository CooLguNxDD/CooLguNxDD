"""Pure declarative multi-breed cat SVG rendering and deterministic selection."""

from __future__ import annotations

import hashlib
import re
from string import Template
from typing import Any, Callable

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

COLOR_KEYS = (
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


def _safe_hex(color: str | None, fallback: str) -> str:
    cleaned = (color or "").strip()
    return cleaned if HEX_COLOR_RE.match(cleaned) else fallback


def select_roaming_breeds(
    breeds_cfg: dict[str, Any],
    count: int,
    seed_str: str,
    *,
    include_pinned: bool = False,
) -> list[str]:
    """Deterministically pick distinct breeds from a seed string."""
    pool = [
        bid for bid, spec in breeds_cfg["breeds"].items()
        if include_pinned or bool(spec.get("roaming"))
    ]
    ranked = sorted(
        pool,
        key=lambda bid: hashlib.sha256(f"{seed_str}:{bid}".encode("utf-8")).hexdigest(),
    )
    return ranked[:count]


def select_daily_poses(poses: list[str], count: int, seed_str: str) -> list[str]:
    """Deterministically shuffle animation poses across strip slots for seed_str."""
    ranked = sorted(
        poses,
        key=lambda pose: hashlib.sha256(f"pose:{seed_str}:{pose}".encode("utf-8")).hexdigest(),
    )
    return ranked[:count]


def select_daily_theme_and_accent(themes_cfg: dict[str, Any], seed_str: str) -> tuple[str, str]:
    """Deterministically pick a roaming dark theme and accent pair for seed_str."""
    theme_pool = [tid for tid, spec in themes_cfg["themes"].items() if bool(spec.get("roaming"))]
    accent_pool = [aid for aid, spec in themes_cfg["accents"].items() if bool(spec.get("roaming"))]
    ranked_themes = sorted(
        theme_pool,
        key=lambda tid: hashlib.sha256(f"theme:{seed_str}:{tid}".encode("utf-8")).hexdigest(),
    )
    ranked_accents = sorted(
        accent_pool,
        key=lambda aid: hashlib.sha256(f"accent:{seed_str}:{aid}".encode("utf-8")).hexdigest(),
    )
    return ranked_themes[0], ranked_accents[0]


def _render_pattern_parts(markings_cfg: dict[str, dict[str, str]], pattern: str, palette: dict[str, str]) -> dict[str, str]:
    spec = markings_cfg[pattern]
    ctx = {"accent": palette["accent"], "accent2": palette["accent2"]}
    return {
        "head_marking": Template(spec["head"]).substitute(ctx),
        "body_marking": Template(spec["body"]).substitute(ctx),
        "tail_marking": Template(spec["tail"]).substitute(ctx),
    }


MARKING_RENDERERS: dict[str, Callable[[dict[str, Any], dict[str, str]], dict[str, str]]] = {
    name: (lambda cfg, pal, pat=name: _render_pattern_parts(cfg, pat, pal))
    for name in ("solid", "tabby", "calico", "tuxedo", "pointed", "nebula")
}


def _render_pose_parts(pose_tmpl: str, palette: dict[str, str], markings: dict[str, str]) -> str:
    return Template(pose_tmpl).substitute(**palette, **markings)


POSE_RENDERERS: dict[str, Callable[[str, dict[str, str], dict[str, str]], str]] = {
    name: _render_pose_parts
    for name in ("sit", "peek", "stroll", "nap", "play", "fish_catch", "stretch", "yarn", "groom", "box")
}


def render_cat(
    breed_id: str,
    pose: str,
    breeds_cfg: dict[str, Any],
    wrapper_tmpl: str,
    *,
    x: float,
    y: float,
    scale: str,
    flip: str = "1",
    fallback: str = "#863bff",
) -> str:
    """Render an animated breed cat positioned inside a static outer wrapper."""
    spec = breeds_cfg["breeds"][breed_id]
    palette = {k: _safe_hex(spec.get(k), fallback) for k in COLOR_KEYS}
    pattern = str(spec["pattern"])
    markings = MARKING_RENDERERS[pattern](breeds_cfg["markings"], palette)
    pose_tmpl = breeds_cfg["poses"][pose]
    inner = POSE_RENDERERS[pose](pose_tmpl, palette, markings)
    return Template(wrapper_tmpl).substitute(x=x, y=y, flip=flip, scale=scale, inner=inner)
