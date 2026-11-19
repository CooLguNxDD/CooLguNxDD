#!/usr/bin/env python3
"""Thin CLI wiring for generating self-hosted profile SVG cards."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

from stats_gen.cats import select_daily_poses, select_daily_theme_and_accent, select_roaming_breeds
from stats_gen.config import apply_theme_preset, load_breeds, load_config, load_text_bundle, load_themes
from stats_gen.github_io import (
    fetch_all_time_contributions,
    fetch_repositories,
    graphql_request,
)
from stats_gen.metrics import derive_metrics
from stats_gen.svg import CARD_RENDERERS

OFFLINE_CARDS = (
    ("hero", "hero-banner.svg"),
    ("catportfolio", "catportfolio-card.svg"),
    ("cat_strip", "cat-fleet-strip.svg"),
    ("ai_tools_cat", "ai-tools-cat.svg"),
)
STATS_CARDS = (("stats", "github-stats.svg"), ("top_langs", "top-langs.svg"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI flags for full stats, offline cards, or hero-only generation."""
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(prog="generate_stats.py")
    parser.add_argument("--hero-only", action="store_true", help="Render hero banner only without network calls")
    parser.add_argument("--offline", action="store_true", help="Render hero, CatPortfolio, and Cat Fleet strip offline")
    parser.add_argument("--daily", action="store_true", help="Rotate dark theme, accent, breeds, and poses daily")
    parser.add_argument("--seed", default="", help="Override deterministic daily breed seed")
    parser.add_argument("--theme", default="", help="Override active theme id from config/themes.json")
    parser.add_argument("--accent", default="", help="Override active accent id from config/themes.json")
    parser.add_argument("--config", type=Path, default=root / "config" / "profile.json", help="Path to profile.json")
    parser.add_argument("--breeds", type=Path, default=root / "config" / "breeds.json", help="Path to breeds.json")
    parser.add_argument("--themes", type=Path, default=root / "config" / "themes.json", help="Path to themes.json")
    parser.add_argument("--output-dir", type=Path, default=root / "assets", help="Directory for generated SVGs")
    return parser.parse_args(argv)


def _resolve_excluded(cfg: dict[str, Any]) -> set[str]:
    if "EXCLUDE_LANGS" in os.environ:
        return {part.strip() for part in os.environ["EXCLUDE_LANGS"].split(",") if part.strip()}
    return {str(x) for x in cfg["excluded_languages"]}


def _write_svg(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _print_summary(
    cfg: dict[str, Any],
    metrics: dict[str, Any],
    ctx: dict[str, Any],
    *,
    username: str,
    custom_token: bool,
    include_private: bool,
) -> None:
    msgs = cfg["messages"]
    langs_str = ", ".join(
        msgs["cli_lang_item"].format(name=item["name"], pct=item["pct"])
        for item in metrics["top_langs"]
    )
    print(msgs["cli_summary_header"].format(username=username, custom_token=custom_token, include_private=include_private))
    print(msgs["cli_summary_contribs"].format(contribs_12m=metrics["contribs_12m"], restricted=metrics["restricted_12m"], all_time=metrics["all_time_contribs"]))
    print(msgs["cli_summary_activity"].format(prs=metrics["prs_total"], active_days=metrics["active_days"], current_streak=metrics["current_streak"], longest_streak=metrics["longest_streak"]))
    print(msgs["cli_summary_repos"].format(repos_count=metrics["repos_count"]))
    print(msgs["cli_summary_langs"].format(langs=langs_str))
    print(msgs["cli_cat_fleet"].format(
        seed=ctx["seed"],
        strip_breeds=",".join(ctx["strip_breed_ids"]),
        theme=cfg["active_theme"],
        accent=cfg["active_accent"],
    ))


def _fetch_all(
    username: str,
    token: str,
    include_private: bool,
    bundle: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    queries, gh_cfg, msgs = bundle["queries"], cfg["github"], cfg["messages"]
    primary_res = graphql_request(queries["primary"], {"login": username}, token, gh_cfg, msgs)
    if not primary_res.ok or not primary_res.data:
        return None, primary_res.error
    user = primary_res.data.get("user")
    if not user:
        return None, msgs["missing_user"].format(username=username)

    years = [int(y) for y in (user["contributionsCollection"].get("contributionYears") or [])]
    all_time_res = fetch_all_time_contributions(username, token, years, queries, gh_cfg, msgs)
    if not all_time_res.ok or not all_time_res.data:
        return None, all_time_res.error

    repos_res = fetch_repositories(username, token, include_private, queries, gh_cfg, msgs, username)
    if not repos_res.ok or not repos_res.data:
        return None, repos_res.error
    return {"user": user, "all_time": int(all_time_res.data["total"]), "repos": repos_res.data}, ""


def _build_cat_context(
    cfg: dict[str, Any],
    breeds_cfg: dict[str, Any],
    bundle: dict[str, dict[str, Any]],
    seed: str,
    *,
    daily_mode: bool,
) -> dict[str, Any]:
    slot_poses = [str(s["pose"]) for s in cfg["cat_strip"]["slots"]]
    if daily_mode:
        chosen = select_roaming_breeds(breeds_cfg, 3 + len(slot_poses), seed, include_pinned=True)
        hero_breed, cp_breed, ai_breed, strip_breeds = chosen[0], chosen[1], chosen[2], chosen[3:]
        all_poses = list(breeds_cfg["poses"].keys())
        chosen_poses = select_daily_poses(all_poses, 1 + len(slot_poses), seed)
        ai_pose, poses = chosen_poses[0], chosen_poses[1:]
    else:
        hero_breed, cp_breed, ai_breed, ai_pose = None, None, None, None
        strip_breeds = select_roaming_breeds(breeds_cfg, len(slot_poses), seed)
        poses = slot_poses
    return {
        "cfg": cfg,
        "breeds": breeds_cfg,
        "templates": bundle["templates"],
        "seed": seed,
        "hero_breed_id": hero_breed,
        "catportfolio_breed_id": cp_breed,
        "ai_tools_breed_id": ai_breed,
        "ai_tools_pose": ai_pose,
        "strip_breed_ids": strip_breeds,
        "strip_poses": poses,
    }


def _write_card_set(cards: tuple[tuple[str, str], ...], ctx: dict[str, Any], out_dir: Path) -> None:
    # Rule 5: one-shot static SVG pipeline uses direct CARD_RENDERERS registry dispatch rather than an event bus.
    for key, filename in cards:
        _write_svg(out_dir / filename, CARD_RENDERERS[key](ctx))


def _light_ctx(ctx: dict[str, Any], raw_cfg: dict[str, Any], themes_cfg: dict[str, Any]) -> dict[str, Any]:
    light_theme = str(raw_cfg.get("light_theme", "latte"))
    light_accent = str(raw_cfg.get("light_accent", "mauve"))
    light_cfg = apply_theme_preset(raw_cfg, themes_cfg, theme_id=light_theme, accent_id=light_accent)
    return {**ctx, "cfg": light_cfg}


def run_full(
    ctx: dict[str, Any],
    raw_cfg: dict[str, Any],
    themes_cfg: dict[str, Any],
    bundle: dict[str, dict[str, Any]],
    out_dir: Path,
) -> int:
    """Fetch GitHub metrics and dispatch all registered SVG card renderers."""
    cfg = ctx["cfg"]
    stats_token = os.environ.get("STATS_TOKEN", "").strip()
    token = stats_token or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print(cfg["messages"]["missing_token"], file=sys.stderr)
        return 1

    has_custom = bool(stats_token)
    include_private = has_custom and os.environ.get("INCLUDE_PRIVATE_REPOS", "true").lower() == "true"
    username = os.environ.get("GITHUB_USERNAME", str(cfg["username"])).strip() or str(cfg["username"])

    fetched, err = _fetch_all(username, token, include_private, bundle, cfg)
    if not fetched:
        print(err, file=sys.stderr)
        return 1

    excluded = _resolve_excluded(cfg)
    metrics = derive_metrics(
        fetched["user"], int(fetched["repos"]["totalCount"]),
        fetched["repos"]["nodes"], fetched["all_time"], excluded, cfg,
    )
    shared = {
        "metrics": metrics,
        "include_private": include_private,
        "has_exclusions": bool(excluded),
        "updated_date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
    }
    full_ctx = {**ctx, **shared}
    all_cards = OFFLINE_CARDS + STATS_CARDS
    _write_card_set(all_cards, full_ctx, out_dir)
    _write_card_set(all_cards, _light_ctx(full_ctx, raw_cfg, themes_cfg), out_dir / "light")
    _print_summary(cfg, metrics, full_ctx, username=username, custom_token=has_custom, include_private=include_private)
    return 0


def _resolve_theme_ids(
    args: argparse.Namespace,
    raw_cfg: dict[str, Any],
    themes_cfg: dict[str, Any],
    seed: str,
    daily_mode: bool,
) -> tuple[str, str]:
    daily_theme, daily_accent = select_daily_theme_and_accent(themes_cfg, seed)
    raw_theme = args.theme.strip() or os.environ.get("CAT_THEME", "").strip()
    raw_accent = args.accent.strip() or os.environ.get("CAT_ACCENT", "").strip()
    default_theme = daily_theme if daily_mode else str(raw_cfg.get("active_theme", "catportfolio"))
    default_accent = daily_accent if daily_mode else str(raw_cfg.get("active_accent", "violet"))
    theme_id = default_theme if raw_theme in ("", "auto") else raw_theme
    accent_id = default_accent if raw_accent in ("", "auto") else raw_accent
    return theme_id, accent_id


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    root = args.config.resolve().parent.parent
    raw_cfg = load_config(args.config)
    themes_cfg = load_themes(args.themes)
    seed = args.seed.strip() or os.environ.get("CAT_SEED", "").strip() or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    daily_mode = args.daily or os.environ.get("CAT_DAILY", "").strip().lower() == "true"
    theme_id, accent_id = _resolve_theme_ids(args, raw_cfg, themes_cfg, seed, daily_mode)
    cfg = apply_theme_preset(raw_cfg, themes_cfg, theme_id=theme_id, accent_id=accent_id)
    breeds_cfg = load_breeds(args.breeds)
    bundle = load_text_bundle(root)
    ctx = _build_cat_context(cfg, breeds_cfg, bundle, seed, daily_mode=daily_mode)

    if args.hero_only:
        _write_card_set((OFFLINE_CARDS[0],), ctx, args.output_dir)
        _write_card_set((OFFLINE_CARDS[0],), _light_ctx(ctx, raw_cfg, themes_cfg), args.output_dir / "light")
        print(cfg["messages"]["cli_hero_done"])
        return 0
    if args.offline:
        _write_card_set(OFFLINE_CARDS, ctx, args.output_dir)
        _write_card_set(OFFLINE_CARDS, _light_ctx(ctx, raw_cfg, themes_cfg), args.output_dir / "light")
        print(cfg["messages"]["cli_hero_done"])
        return 0
    return run_full(ctx, raw_cfg, themes_cfg, bundle, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
