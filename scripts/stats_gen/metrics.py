"""Pure metric derivation, color normalization, and bar allocation."""

from __future__ import annotations

from typing import Any


def compute_streaks(days: list[dict[str, Any]]) -> tuple[int, int]:
    """Return (current_streak, longest_streak) from contribution days."""
    ordered = sorted(days, key=lambda d: str(d["date"]))
    longest = 0
    run = 0
    for day in ordered:
        if int(day["contributionCount"]) > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    if not ordered:
        return 0, longest

    idx = len(ordered) - 1
    if int(ordered[idx]["contributionCount"]) == 0:
        idx -= 1

    current = 0
    while idx >= 0 and int(ordered[idx]["contributionCount"]) > 0:
        current += 1
        idx -= 1
    return current, longest


def relative_luminance(hex_color: str, norm_cfg: dict[str, Any]) -> float:
    """Compute WCAG relative luminance for a #RRGGBB color string."""
    cleaned = hex_color.lstrip("#")
    if len(cleaned) != 6:
        return 0.5
    try:
        rgb = bytes.fromhex(cleaned)
    except ValueError:
        return 0.5

    cmax = float(norm_cfg["channel_max"])
    cutoff = float(norm_cfg["srgb_linear_cutoff"])
    div = float(norm_cfg["srgb_linear_div"])
    offset = float(norm_cfg["srgb_offset"])
    scale = float(norm_cfg["srgb_scale"])
    gamma = float(norm_cfg["srgb_gamma"])
    lin = [
        (b / cmax) / div if (b / cmax) <= cutoff else (((b / cmax) + offset) / scale) ** gamma
        for b in rgb
    ]
    return (
        float(norm_cfg["luma_r"]) * lin[0]
        + float(norm_cfg["luma_g"]) * lin[1]
        + float(norm_cfg["luma_b"]) * lin[2]
    )


def normalize_lang_color(raw_color: str | None, norm_cfg: dict[str, Any], default_color: str) -> str:
    """Lift dark language colors so dots and bar segments stay legible."""
    if not raw_color:
        return default_color
    color = raw_color.strip().lower()
    overrides = norm_cfg["overrides"]
    if color in overrides:
        return str(overrides[color])
    if len(color) != 7 or not color.startswith("#"):
        return default_color
    if relative_luminance(color, norm_cfg) >= float(norm_cfg["min_luminance"]):
        return color

    try:
        src = bytes.fromhex(color[1:])
        dst = bytes.fromhex(str(norm_cfg["blend_target"]).lstrip("#"))
    except ValueError:
        return default_color
    w = float(norm_cfg["blend_weight"])
    blended = [int(a * (1.0 - w) + b * w) for a, b in zip(src, dst)]
    return f"#{blended[0]:02x}{blended[1]:02x}{blended[2]:02x}"


def _collect_repo_langs(
    repos: list[dict[str, Any]],
    excluded: set[str],
    norm_cfg: dict[str, Any],
    default_color: str,
) -> tuple[dict[str, int], dict[str, str]]:
    totals: dict[str, int] = {}
    colors: dict[str, str] = {}
    for repo in repos:
        edges = ((repo.get("languages") or {}).get("edges")) or []
        for edge in edges:
            node = edge.get("node") or {}
            name = str(node.get("name") or "").strip()
            size = int(edge.get("size") or 0)
            if not name or size <= 0 or name in excluded:
                continue
            totals[name] = totals.get(name, 0) + size
            colors.setdefault(name, normalize_lang_color(node.get("color"), norm_cfg, default_color))
    return totals, colors


def aggregate_languages(
    repos: list[dict[str, Any]],
    excluded: set[str],
    norm_cfg: dict[str, Any],
    default_color: str,
    limit: int,
    pct_scale: float,
) -> list[dict[str, Any]]:
    """Aggregate byte totals across repos, filter exclusions, and rank top N."""
    totals, colors = _collect_repo_langs(repos, excluded, norm_cfg, default_color)
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
    grand_total = sum(size for _, size in ranked)
    if grand_total <= 0:
        return []
    return [
        {
            "name": name,
            "size": size,
            "pct": (size / grand_total) * pct_scale,
            "color": colors.get(name, default_color),
        }
        for name, size in ranked
    ]


def format_compact(value: int, fmt_cfg: dict[str, Any]) -> str:
    """Format large integer values compactly for the circular ring center."""
    k_unit = int(fmt_cfg["compact_thousand"])
    large_unit = int(fmt_cfg["compact_hundred_k"])
    suffix = str(fmt_cfg["compact_suffix"])
    if value < k_unit:
        return str(value)
    if value < large_unit:
        return f"{value / k_unit:.1f}{suffix}"
    return f"{value // k_unit}{suffix}"


def allocate_bar_widths(langs: list[dict[str, Any]], total_width: float, min_width: float) -> list[float]:
    """Distribute exact segment widths summing to total_width with a floor."""
    if not langs:
        return []
    total_size = sum(int(item["size"]) for item in langs)
    if total_size <= 0:
        return []

    raw = [total_width * (int(item["size"]) / total_size) for item in langs]
    small = {i for i, w in enumerate(raw) if w < min_width}
    if small and len(small) < len(raw):
        remaining = total_width - len(small) * min_width
        large_sum = sum(raw[i] for i in range(len(raw)) if i not in small)
        if large_sum > 0 and remaining > 0:
            raw = [
                min_width if i in small else (raw[i] / large_sum) * remaining
                for i in range(len(raw))
            ]

    if len(raw) > 1:
        raw[-1] = max(0.0, total_width - sum(raw[:-1]))
    else:
        raw[0] = total_width
    return raw


def derive_metrics(
    primary_user: dict[str, Any],
    repos_count: int,
    repos: list[dict[str, Any]],
    all_time_raw: int,
    excluded: set[str],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Compute all normalized profile metrics from raw GraphQL payloads."""
    cc = primary_user["contributionsCollection"]
    cal = cc["contributionCalendar"]
    weeks = cal.get("weeks") or []
    days = [day for week in weeks for day in (week.get("contributionDays") or [])]
    contribs_12m = int(cal["totalContributions"])
    current_streak, longest_streak = compute_streaks(days)
    spark_weeks = int(cfg["stats_card"]["sparkline"]["weeks"])
    weekly_totals = [
        sum(int(d["contributionCount"]) for d in (w.get("contributionDays") or []))
        for w in weeks[-spark_weeks:]
    ]
    top_langs = aggregate_languages(
        repos,
        excluded,
        cfg["color_norm"],
        cfg["theme"]["default_lang_color"],
        int(cfg["langs_card"]["limit"]),
        float(cfg["formatting"]["pct_scale"]),
    )
    return {
        "contribs_12m": contribs_12m,
        "restricted_12m": int(cc["restrictedContributionsCount"]),
        "all_time_contribs": max(contribs_12m, int(all_time_raw)),
        "prs_total": int(primary_user["pullRequests"]["totalCount"]),
        "active_days": sum(1 for day in days if int(day["contributionCount"]) > 0),
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "weekly_totals": weekly_totals,
        "repos_count": repos_count,
        "top_langs": top_langs,
    }
