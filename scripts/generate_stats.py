#!/usr/bin/env python3
"""Thin CLI wiring for generating self-hosted profile SVG cards."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

from stats_gen.config import load_config, load_text_bundle
from stats_gen.github_io import (
    fetch_all_time_contributions,
    fetch_repositories,
    graphql_request,
)
from stats_gen.metrics import derive_metrics
from stats_gen.svg import CARD_RENDERERS


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI flags for full stats or offline hero generation."""
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(prog="generate_stats.py")
    parser.add_argument("--hero-only", action="store_true", help="Render hero banner only without network calls")
    parser.add_argument("--config", type=Path, default=root / "config" / "profile.json", help="Path to profile.json")
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


def run_full(cfg: dict[str, Any], bundle: dict[str, dict[str, Any]], out_dir: Path) -> int:
    """Fetch GitHub metrics and dispatch all registered SVG card renderers."""
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
        fetched["user"],
        int(fetched["repos"]["totalCount"]),
        fetched["repos"]["nodes"],
        fetched["all_time"],
        excluded,
        cfg,
    )
    ctx = {
        "cfg": cfg,
        "templates": bundle["templates"],
        "metrics": metrics,
        "include_private": include_private,
        "has_exclusions": bool(excluded),
        "updated_date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
    }
    # Rule 5: one-shot static SVG pipeline uses direct CARD_RENDERERS registry dispatch rather than an event bus.
    targets = (("hero", "hero-banner.svg"), ("stats", "github-stats.svg"), ("top_langs", "top-langs.svg"))
    for key, filename in targets:
        _write_svg(out_dir / filename, CARD_RENDERERS[key](ctx))
    _print_summary(cfg, metrics, username=username, custom_token=has_custom, include_private=include_private)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    root = args.config.resolve().parent.parent
    cfg = load_config(args.config)
    bundle = load_text_bundle(root)
    if args.hero_only:
        _write_svg(args.output_dir / "hero-banner.svg", CARD_RENDERERS["hero"]({"cfg": cfg, "templates": bundle["templates"]}))
        print(cfg["messages"]["cli_hero_done"])
        return 0
    return run_full(cfg, bundle, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
