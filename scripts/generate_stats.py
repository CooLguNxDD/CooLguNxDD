#!/usr/bin/env python3
"""Generate self-hosted GitHub stats and top-languages SVG cards."""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
USER_AGENT = "CooLguNxDD-profile-stats"

DEFAULT_EXCLUDED_LANGUAGES = {
    "Jupyter Notebook",
    "ShaderLab",
    "Batchfile",
    "HTML",
    "CSS",
    "SCSS",
    "ASP.NET",
    "Mathematica",
    "Makefile",
    "CMake",
    "Rich Text Format",
    "TeX",
}

LANGUAGE_COLOR_OVERRIDES = {
    "#555555": "#8b9bb4",  # C
    "#222c37": "#6c83a3",  # ShaderLab
    "#191919": "#8b9bb4",
    "#000000": "#8b9bb4",
}

FONT_FAMILY = "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"

PRIMARY_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    pullRequests(first: 1) {
      totalCount
    }
    issues(first: 1) {
      totalCount
    }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      contributionYears
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

REPOS_QUERY_TEMPLATE = """
query($login: String!, $cursor: String) {{
  user(login: $login) {{
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false{privacy_arg}, after: $cursor) {{
      totalCount
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        name
        isPrivate
        stargazerCount
        languages(first: 20, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


def graphql_request(query: str, variables: dict[str, object], token: str) -> dict[str, object]:
    """Execute a GitHub GraphQL request and fail loudly on HTTP or GraphQL errors."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub GraphQL network error: {exc.reason}") from exc

    parsed = json.loads(body)
    if parsed.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {json.dumps(parsed['errors'], indent=2)}")
    data = parsed.get("data")
    if not data:
        raise RuntimeError(f"GitHub GraphQL returned no data: {body}")
    return data


def fetch_repositories(login: str, token: str, include_private: bool) -> tuple[int, list[dict[str, object]]]:
    """Fetch all owned non-fork repositories via paginated GraphQL queries."""
    privacy_arg = "" if include_private else ", privacy: PUBLIC"
    query = REPOS_QUERY_TEMPLATE.format(privacy_arg=privacy_arg)
    nodes: list[dict[str, object]] = []
    cursor: str | None = None
    total_count = 0

    while True:
        data = graphql_request(query, {"login": login, "cursor": cursor}, token)
        user = data.get("user")
        if not user:
            raise RuntimeError(f"User '{login}' not found while fetching repositories")
        repo_conn = user["repositories"]
        total_count = int(repo_conn["totalCount"])
        nodes.extend(repo_conn.get("nodes") or [])
        page_info = repo_conn["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    return total_count, nodes


def fetch_all_time_contributions(login: str, token: str, years: list[int]) -> int:
    """Sum contributionCalendar.totalContributions across all active years."""
    if not years:
        return 0
    unique_years = sorted({int(y) for y in years})
    parts = "\n".join(
        f'    y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{y}-12-31T23:59:59Z") '
        "{ contributionCalendar { totalContributions } }"
        for y in unique_years
    )
    query = f"query($login: String!) {{\n  user(login: $login) {{\n{parts}\n  }}\n}}"
    data = graphql_request(query, {"login": login}, token)
    user = data.get("user") or {}
    return sum(
        int(user[f"y{y}"]["contributionCalendar"]["totalContributions"])
        for y in unique_years
        if user.get(f"y{y}")
    )


def compute_streaks(days: list[dict[str, object]]) -> tuple[int, int]:
    """Compute (current_streak, longest_streak) from chronological contribution days."""
    ordered = sorted(days, key=lambda d: str(d["date"]))
    longest = 0
    run = 0
    for day in ordered:
        if int(day["contributionCount"]) > 0:
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0

    if not ordered:
        return 0, longest

    # Allow today (final entry) to be 0 if not yet committed today, as long as yesterday is active.
    idx = len(ordered) - 1
    if int(ordered[idx]["contributionCount"]) == 0:
        idx -= 1

    current = 0
    while idx >= 0 and int(ordered[idx]["contributionCount"]) > 0:
        current += 1
        idx -= 1

    return current, longest


def relative_luminance(hex_color: str) -> float:
    """Compute WCAG relative luminance for a #RRGGBB color string."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 0.5
    channels = []
    for i in (0, 2, 4):
        srgb = int(h[i : i + 2], 16) / 255.0
        lin = srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4
        channels.append(lin)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def normalize_lang_color(raw_color: str | None) -> str:
    """Ensure language colors remain legible against the #14111f card surface."""
    if not raw_color:
        return "#863bff"
    color = raw_color.strip().lower()
    if color in LANGUAGE_COLOR_OVERRIDES:
        return LANGUAGE_COLOR_OVERRIDES[color]
    if relative_luminance(color) < 0.08 and len(color) == 7 and color.startswith("#"):
        # Blend overly dark colors toward soft lavender (#b5abc8) for contrast.
        r = int(int(color[1:3], 16) * 0.5 + 0xB5 * 0.5)
        g = int(int(color[3:5], 16) * 0.5 + 0xAB * 0.5)
        b = int(int(color[5:7], 16) * 0.5 + 0xC8 * 0.5)
        return f"#{r:02x}{g:02x}{b:02x}"
    return color


def aggregate_languages(
    repos: list[dict[str, object]],
    excluded_languages: set[str],
    limit: int = 8,
) -> list[dict[str, object]]:
    """Aggregate byte counts across repos, exclude noisy types, and return top N entries."""
    totals: dict[str, int] = {}
    colors: dict[str, str] = {}

    for repo in repos:
        edges = ((repo.get("languages") or {}).get("edges")) or []
        for edge in edges:
            node = edge.get("node") or {}
            name = str(node.get("name") or "").strip()
            size = int(edge.get("size") or 0)
            if not name or size <= 0 or name in excluded_languages:
                continue
            totals[name] = totals.get(name, 0) + size
            if name not in colors:
                colors[name] = normalize_lang_color(node.get("color"))

    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
    grand_total = sum(size for _, size in ranked)
    if grand_total <= 0:
        return []

    return [
        {
            "name": name,
            "size": size,
            "pct": (size / grand_total) * 100.0,
            "color": colors.get(name, "#863bff"),
        }
        for name, size in ranked
    ]


def format_compact(value: int) -> str:
    """Format large integers compactly for the circular ring center label."""
    if value < 1000:
        return str(value)
    if value < 100_000:
        return f"{value / 1000:.1f}k"
    return f"{value // 1000}k"


def allocate_bar_widths(langs: list[dict[str, object]], total_width: float = 332.0, min_width: float = 2.0) -> list[float]:
    """Compute segment widths summing strictly to total_width with a min_width floor."""
    if not langs:
        return []
    total_size = sum(int(item["size"]) for item in langs)
    if total_size <= 0:
        return []

    raw = [total_width * (int(item["size"]) / total_size) for item in langs]
    small = [i for i, w in enumerate(raw) if w < min_width]
    if small and len(small) < len(raw):
        locked = len(small) * min_width
        remaining = total_width - locked
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


def render_stats_svg(
    *,
    contribs_12m: int,
    all_time_contribs: int,
    prs_total: int,
    active_days: int,
    current_streak: int,
    longest_streak: int,
    weekly_totals: list[int],
    repos_count: int,
    includes_private_repos: bool,
    updated_date: str,
) -> str:
    """Render the GitHub Activity card SVG matching the CatPortfolio theme."""
    radius = 36.0
    circumference = 2.0 * math.pi * radius
    active_fraction = min(1.0, max(0.0, active_days / 365.0))
    dash_len = active_fraction * circumference

    if current_streak > 0:
        pill_text = f"{current_streak}d STREAK"
        pill_inner = (
            '    <g transform="translate(335, 23) scale(0.75)">\n'
            '      <path d="M8.5 1.5c.3 2.1-1.2 3.4-2.2 4.6-.9 1.1-1.5 2.1-1.3 3.5.2 1.2 1.1 2.2 2.4 2.4 1.6.3 3.1-.8 3.3-2.5.1-1.1-.4-2.1-.9-2.9 1.5.5 2.8 1.9 2.9 3.8.2 2.7-2 5-4.7 5-2.8 0-5-2.1-5-4.9 0-2.1 1.1-3.8 2.4-5.2 1.2-1.3 2.5-2.4 3.1-3.8Z" fill="#7ee0ff"/>\n'
            "    </g>\n"
            f'    <text x="383" y="32.5" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="10" font-weight="700" fill="#d7c2ff">{html.escape(pill_text)}</text>'
        )
    else:
        pill_text = f"{longest_streak}d BEST"
        pill_inner = (
            f'    <text x="376" y="32.5" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="10" font-weight="700" fill="#d7c2ff">{html.escape(pill_text)}</text>'
        )

    rows = [
        (
            66,
            "Contributions (12m)",
            f"{contribs_12m:,}",
            "#47bfff",
            "M1 2.75A1.75 1.75 0 0 1 2.75 1h10.5A1.75 1.75 0 0 1 15 2.75v10.5A1.75 1.75 0 0 1 13.25 15H2.75A1.75 1.75 0 0 1 1 13.25V2.75Zm2.5 1.25v2.5h2.5V4H3.5Zm4 0v2.5h2.5V4H7.5Zm4 0v2.5H14V4h-2.5Zm-8 4v2.5h2.5V8H3.5Zm4 0v2.5h2.5V8H7.5Zm4 0v2.5H14V8h-2.5Z",
        ),
        (
            94,
            "All-Time Contribs",
            f"{all_time_contribs:,}",
            "#a77bff",
            "M8 1.5l1.95 3.95 4.36.63-3.15 3.07.74 4.34L8 11.44l-3.9 2.05.74-4.34-3.15-3.07 4.36-.63L8 1.5z",
        ),
        (
            122,
            "Pull Requests",
            f"{prs_total:,}",
            "#47bfff",
            "M5 3.25a1.75 1.75 0 1 1-3.5 0 1.75 1.75 0 0 1 3.5 0Zm9.5 9.5a1.75 1.75 0 1 1-3.5 0 1.75 1.75 0 0 1 3.5 0Zm-9.5 0a1.75 1.75 0 1 1-3.5 0 1.75 1.75 0 0 1 3.5 0ZM3.25 5v6M12.75 11V6.5a2 2 0 0 0-2-2H8.5m0 0 1.75-1.75M8.5 4.5l1.75 1.75",
        ),
        (
            150,
            "Active Days (12m)",
            f"{active_days} / 365",
            "#a77bff",
            "M8 2.2a5.8 5.8 0 1 0 5.8 5.8A5.8 5.8 0 0 0 8 2.2Zm0 1.8v4.2l2.8 1.7",
        ),
    ]

    row_blocks: list[str] = []
    for idx, (y, label, value, icon_color, path_d) in enumerate(rows):
        icon_y = y - 11.5
        if idx in (2, 3):
            icon_elem = (
                f'<path d="{path_d}" fill="none" stroke="{icon_color}" stroke-width="1.6" '
                f'stroke-linecap="round" stroke-linejoin="round"/>'
            )
        else:
            icon_elem = f'<path d="{path_d}" fill="{icon_color}"/>'

        row_blocks.append(
            f'  <g transform="translate(24, {icon_y:.1f}) scale(0.875)">{icon_elem}</g>\n'
            f'  <text x="46" y="{y}" font-family="{FONT_FAMILY}" font-size="12.5" fill="#b5abc8">{html.escape(label)}</text>\n'
            f'  <text x="256" y="{y}" text-anchor="end" font-family="{FONT_FAMILY}" font-size="13.5" font-weight="700" fill="#f4eefc">{html.escape(value)}</text>'
        )

    padded_weeks = ([0] * max(0, 12 - len(weekly_totals))) + weekly_totals[-12:]
    max_week = max(padded_weeks) if padded_weeks else 0
    spark_bars: list[str] = []
    for i, count in enumerate(padded_weeks):
        bx = 286.0 + i * 11.5
        if count <= 0 or max_week <= 0:
            spark_bars.append(
                f'  <rect x="{bx:.1f}" y="171.0" width="8" height="3.0" rx="2" fill="#2a2440"/>'
            )
        else:
            bh = max(3.0, (count / max_week) * 26.0)
            by = 174.0 - bh
            spark_bars.append(
                f'  <rect x="{bx:.1f}" y="{by:.1f}" width="8" height="{bh:.1f}" rx="2" fill="url(#spark)"/>'
            )

    ring_arc = ""
    if active_fraction > 0:
        ring_arc = (
            f'  <circle cx="353" cy="98" r="36" fill="none" stroke="url(#ring)" stroke-width="6" '
            f'stroke-linecap="round" stroke-dasharray="{dash_len:.2f} {circumference:.2f}" '
            f'transform="rotate(-90 353 98)"/>\n'
        )

    scope_word = "repos" if includes_private_repos else "public repos"
    footer = f"{repos_count} {scope_word} · updated {updated_date}"
    desc = (
        f"{contribs_12m:,} contributions in the last 12 months, "
        f"{all_time_contribs:,} all-time contributions, "
        f"{prs_total:,} pull requests, {active_days} active days."
    )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="450" height="196" viewBox="0 0 450 196" '
        'role="img" aria-labelledby="stats-title stats-desc">\n'
        '  <title id="stats-title">GitHub Activity</title>\n'
        f'  <desc id="stats-desc">{html.escape(desc)}</desc>\n'
        "  <defs>\n"
        '    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">\n'
        '      <stop offset="0%" stop-color="#14111f"/>\n'
        '      <stop offset="100%" stop-color="#1c1730"/>\n'
        "    </linearGradient>\n"
        '    <linearGradient id="accent" x1="0" y1="0" x2="0" y2="1">\n'
        '      <stop offset="0%" stop-color="#863bff"/>\n'
        '      <stop offset="100%" stop-color="#47bfff"/>\n'
        "    </linearGradient>\n"
        '    <linearGradient id="ring" gradientUnits="userSpaceOnUse" x1="317" y1="62" x2="389" y2="134">\n'
        '      <stop offset="0%" stop-color="#863bff"/>\n'
        '      <stop offset="100%" stop-color="#47bfff"/>\n'
        "    </linearGradient>\n"
        '    <linearGradient id="spark" gradientUnits="userSpaceOnUse" x1="0" y1="148" x2="0" y2="174">\n'
        '      <stop offset="0%" stop-color="#47bfff"/>\n'
        '      <stop offset="100%" stop-color="#863bff"/>\n'
        "    </linearGradient>\n"
        '    <clipPath id="card-clip">\n'
        '      <rect x="0" y="0" width="450" height="196" rx="12"/>\n'
        "    </clipPath>\n"
        "  </defs>\n\n"
        '  <rect x="0.5" y="0.5" width="449" height="195" rx="12" fill="url(#bg)" stroke="#3a3358" stroke-width="1"/>\n'
        '  <rect x="0" y="0" width="5" height="196" fill="url(#accent)" clip-path="url(#card-clip)"/>\n\n'
        f'  <text x="24" y="34" font-family="{FONT_FAMILY}" font-size="16" font-weight="700" fill="#f4eefc">GitHub Activity</text>\n'
        "  <g>\n"
        '    <rect x="324" y="18" width="104" height="22" rx="11" fill="#863bff" fill-opacity="0.22" stroke="#863bff" stroke-opacity="0.65"/>\n'
        f"{pill_inner}\n"
        "  </g>\n\n"
        + "\n".join(row_blocks)
        + "\n\n"
        '  <circle cx="353" cy="98" r="36" fill="none" stroke="#2a2440" stroke-width="6"/>\n'
        + ring_arc
        + f'  <text x="353" y="96" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="20" font-weight="700" fill="#f4eefc">{html.escape(format_compact(contribs_12m))}</text>\n'
        f'  <text x="353" y="112" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="9.5" fill="#b5abc8">12m contribs</text>\n\n'
        + "\n".join(spark_bars)
        + "\n\n"
        f'  <text x="24" y="181" font-family="{FONT_FAMILY}" font-size="10" fill="#7f7699">{html.escape(footer)}</text>\n'
        "</svg>\n"
    )


def render_langs_svg(
    *,
    langs: list[dict[str, object]],
    repos_count: int,
    includes_private_repos: bool,
    has_exclusions: bool,
) -> str:
    """Render the Top Languages card SVG matching the CatPortfolio theme."""
    widths = allocate_bar_widths(langs, total_width=332.0, min_width=2.0)
    bar_segments: list[str] = []
    cur_x = 24.0
    for lang, seg_w in zip(langs, widths):
        bar_segments.append(
            f'    <rect x="{cur_x:.2f}" y="50" width="{seg_w:.2f}" height="10" fill="{html.escape(str(lang["color"]))}"/>'
        )
        cur_x += seg_w

    legend_items: list[str] = []
    y_positions = (84, 108, 132, 156)
    for idx, lang in enumerate(langs[:8]):
        col = idx // 4
        row = idx % 4
        y = y_positions[row]
        cx = 29 if col == 0 else 203
        name_x = 41 if col == 0 else 215
        pct_x = 176 if col == 0 else 356

        raw_name = str(lang["name"])
        display_name = raw_name if len(raw_name) <= 13 else f"{raw_name[:12]}…"
        pct_str = f"{float(lang['pct']):.1f}%"
        color = str(lang["color"])

        legend_items.append(
            f'  <circle cx="{cx}" cy="{y - 4}" r="4.5" fill="{html.escape(color)}"/>\n'
            f'  <text x="{name_x}" y="{y}" font-family="{FONT_FAMILY}" font-size="12" fill="#f4eefc">{html.escape(display_name)}</text>\n'
            f'  <text x="{pct_x}" y="{y}" text-anchor="end" font-family="{FONT_FAMILY}" font-size="11.5" fill="#b5abc8">{html.escape(pct_str)}</text>'
        )

    scope_word = "repos" if includes_private_repos else "public repos"
    if has_exclusions:
        footer = f"Across {repos_count} {scope_word} · excludes markup & notebooks"
    else:
        footer = f"Across {repos_count} {scope_word}"

    desc = ", ".join(f"{item['name']} {float(item['pct']):.1f}%" for item in langs) or "No language data"

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="380" height="196" viewBox="0 0 380 196" '
        'role="img" aria-labelledby="langs-title langs-desc">\n'
        '  <title id="langs-title">Top Languages</title>\n'
        f'  <desc id="langs-desc">{html.escape(desc)}</desc>\n'
        "  <defs>\n"
        '    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">\n'
        '      <stop offset="0%" stop-color="#14111f"/>\n'
        '      <stop offset="100%" stop-color="#1c1730"/>\n'
        "    </linearGradient>\n"
        '    <linearGradient id="accent" x1="0" y1="0" x2="0" y2="1">\n'
        '      <stop offset="0%" stop-color="#863bff"/>\n'
        '      <stop offset="100%" stop-color="#47bfff"/>\n'
        "    </linearGradient>\n"
        '    <clipPath id="card-clip">\n'
        '      <rect x="0" y="0" width="380" height="196" rx="12"/>\n'
        "    </clipPath>\n"
        '    <clipPath id="bar-clip">\n'
        '      <rect x="24" y="50" width="332" height="10" rx="5"/>\n'
        "    </clipPath>\n"
        "  </defs>\n\n"
        '  <rect x="0.5" y="0.5" width="379" height="195" rx="12" fill="url(#bg)" stroke="#3a3358" stroke-width="1"/>\n'
        '  <rect x="0" y="0" width="5" height="196" fill="url(#accent)" clip-path="url(#card-clip)"/>\n\n'
        f'  <text x="24" y="34" font-family="{FONT_FAMILY}" font-size="16" font-weight="700" fill="#f4eefc">Top Languages</text>\n'
        "  <g>\n"
        '    <rect x="276" y="18" width="82" height="22" rx="11" fill="#47bfff" fill-opacity="0.15" stroke="#47bfff" stroke-opacity="0.6"/>\n'
        f'    <text x="317" y="32.5" text-anchor="middle" font-family="{FONT_FAMILY}" font-size="10" font-weight="700" fill="#7ee0ff">CORE CODE</text>\n'
        "  </g>\n\n"
        '  <rect x="24" y="50" width="332" height="10" rx="5" fill="#2a2440"/>\n'
        '  <g clip-path="url(#bar-clip)">\n'
        + "\n".join(bar_segments)
        + "\n  </g>\n\n"
        + "\n".join(legend_items)
        + "\n\n"
        f'  <text x="24" y="181" font-family="{FONT_FAMILY}" font-size="10" fill="#7f7699">{html.escape(footer)}</text>\n'
        "</svg>\n"
    )


def main() -> int:
    stats_token = os.environ.get("STATS_TOKEN", "").strip()
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    token = stats_token or github_token
    if not token:
        print("Error: set STATS_TOKEN or GITHUB_TOKEN before running generate_stats.py", file=sys.stderr)
        return 1

    has_custom_token = bool(stats_token)
    include_private = has_custom_token and os.environ.get("INCLUDE_PRIVATE_REPOS", "true").lower() == "true"
    username = os.environ.get("GITHUB_USERNAME", "CooLguNxDD").strip() or "CooLguNxDD"

    if "EXCLUDE_LANGS" in os.environ:
        excluded_languages = {
            part.strip() for part in os.environ["EXCLUDE_LANGS"].split(",") if part.strip()
        }
    else:
        excluded_languages = set(DEFAULT_EXCLUDED_LANGUAGES)

    primary = graphql_request(PRIMARY_QUERY, {"login": username}, token)
    user = primary.get("user")
    if not user:
        print(f"Error: GitHub user '{username}' not found", file=sys.stderr)
        return 1

    cc = user["contributionsCollection"]
    cal = cc["contributionCalendar"]
    contribs_12m = int(cal["totalContributions"])
    restricted_12m = int(cc["restrictedContributionsCount"])
    prs_total = int(user["pullRequests"]["totalCount"])
    years = [int(y) for y in (cc.get("contributionYears") or [])]

    weeks = cal.get("weeks") or []
    days = [day for week in weeks for day in (week.get("contributionDays") or [])]
    active_days = sum(1 for day in days if int(day["contributionCount"]) > 0)
    current_streak, longest_streak = compute_streaks(days)
    weekly_totals = [
        sum(int(day["contributionCount"]) for day in (week.get("contributionDays") or []))
        for week in weeks[-12:]
    ]

    all_time_raw = fetch_all_time_contributions(username, token, years)
    all_time_contribs = max(contribs_12m, all_time_raw)

    repos_count, repos = fetch_repositories(username, token, include_private=include_private)
    top_langs = aggregate_languages(repos, excluded_languages=excluded_languages, limit=8)

    updated_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    root = Path(__file__).resolve().parent.parent
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    stats_svg = render_stats_svg(
        contribs_12m=contribs_12m,
        all_time_contribs=all_time_contribs,
        prs_total=prs_total,
        active_days=active_days,
        current_streak=current_streak,
        longest_streak=longest_streak,
        weekly_totals=weekly_totals,
        repos_count=repos_count,
        includes_private_repos=include_private,
        updated_date=updated_date,
    )
    langs_svg = render_langs_svg(
        langs=top_langs,
        repos_count=repos_count,
        includes_private_repos=include_private,
        has_exclusions=bool(excluded_languages),
    )

    (assets_dir / "github-stats.svg").write_text(stats_svg, encoding="utf-8", newline="\n")
    (assets_dir / "top-langs.svg").write_text(langs_svg, encoding="utf-8", newline="\n")

    print(f"Generated stats for @{username} (custom_token={has_custom_token}, include_private={include_private})")
    print(f"  contribs_12m={contribs_12m} (restricted={restricted_12m}), all_time_contribs={all_time_contribs}")
    print(f"  prs_total={prs_total}, active_days={active_days}, current_streak={current_streak}, longest_streak={longest_streak}")
    print(f"  repos_count={repos_count}")
    print("  top_langs=" + ", ".join(f"{item['name']} ({item['pct']:.1f}%)" for item in top_langs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
