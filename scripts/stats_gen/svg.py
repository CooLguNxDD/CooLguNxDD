"""Pure SVG rendering using declarative templates, snippets, and registries."""

from __future__ import annotations

import html
import math
import re
from string import Template
from typing import Any, Callable

from .cats import render_cat
from .metrics import allocate_bar_widths, format_compact

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SEED_CLEAN_RE = re.compile(r"[^A-Za-z0-9_.:-]")

FISH_DIR_SCALE = {"right": "1", "left": "-1"}
STATS_FOOTER_KEYS = {True: "stats_footer_all", False: "stats_footer_public"}
LANGS_FOOTER_KEYS = {
    (True, True): "langs_footer_all_excl",
    (False, True): "langs_footer_public_excl",
    (True, False): "langs_footer_all_all",
    (False, False): "langs_footer_public_all",
}


def sanitize_color(color: str | None, fallback: str) -> str:
    """Validate #RRGGBB hex color or return fallback."""
    cleaned = (color or "").strip()
    return cleaned if HEX_COLOR_RE.match(cleaned) else fallback


def _esc(val: object) -> str:
    return html.escape(str(val), quote=True)


def _safe_seed(seed: str) -> str:
    return SEED_CLEAN_RE.sub("", seed).replace("--", "-") or "default"


ICON_RENDERERS: dict[str, Callable[[dict[str, str], str, str], str]] = {
    "fill": lambda snips, path, color: Template(snips["icon_fill"]).substitute(path=path, color=color),
    "stroke": lambda snips, path, color: Template(snips["icon_stroke"]).substitute(path=path, color=color),
}

VALUE_FORMATTERS: dict[str, Callable[[Any, dict[str, Any]], str]] = {
    "comma": lambda val, _cfg: f"{int(val):,}",
    "ratio_year": lambda val, cfg: f"{int(val)} / {int(cfg['formatting']['year_days'])}",
}


def render_card_shell(
    cfg: dict[str, Any],
    templates: dict[str, dict[str, str]],
    *,
    width: int,
    height: int,
    clip_id: str,
    title_id: str,
    desc_id: str,
    title: str,
    desc: str,
    extra_defs: str,
    body: str,
) -> str:
    """Render the shared outer SVG frame from card_shell.svg.tmpl."""
    theme = cfg["theme"]
    shell_tmpl = Template(templates["card_shell"]["body"])
    return shell_tmpl.substitute(
        width=width,
        height=height,
        inner_width=width - 1,
        inner_height=height - 1,
        clip_id=_esc(clip_id),
        title_id=_esc(title_id),
        desc_id=_esc(desc_id),
        title=_esc(title),
        desc=_esc(desc),
        bg_start=theme["bg_start"],
        bg_end=theme["bg_end"],
        border=theme["border"],
        accent_start=theme["accent_start"],
        accent_end=theme["accent_end"],
        card_radius=int(theme["card_radius"]),
        accent_width=int(theme["accent_width"]),
        extra_defs=extra_defs,
        body=body,
    )


def _render_hero_chips(hero: dict[str, Any], theme: dict[str, Any], snips: dict[str, str]) -> str:
    tmpl = Template(snips["hero_chip"])
    return "\n".join(
        tmpl.substitute(
            x=chip["x"], y=chip["y"], width=chip["width"], height=chip["height"],
            chip_bg=theme["chip_bg"], chip_border=theme["chip_border"],
            text_x=chip["text_x"], text_y=chip["text_y"],
            font_family=_esc(theme["font_family"]),
            color=sanitize_color(chip["color"], theme["text_primary"]),
            text=_esc(chip["text"]),
        )
        for chip in hero["chips"]
    )


def _render_aquarium_parts(aq: dict[str, Any], theme: dict[str, Any], snips: dict[str, str]) -> dict[str, str]:
    fallback = theme["default_lang_color"]
    kelp_t, minnow_t = Template(snips["aquarium_kelp"]), Template(snips["aquarium_minnow"])
    fish_t, bubble_t = Template(snips["aquarium_fish"]), Template(snips["aquarium_bubble"])
    kelp = "\n".join(
        kelp_t.substitute(
            x=k["x"], y=k["y"], dur_sec=k["dur_sec"], delay_sec=k["delay_sec"],
            path=_esc(k["path"]), color=sanitize_color(k["color"], fallback),
        )
        for k in aq["kelp"]
    )
    minnows = "\n".join(
        minnow_t.substitute(
            x=m["x"], y=m["y"], dur_sec=m["dur_sec"], delay_sec=m["delay_sec"],
            dir_scale=FISH_DIR_SCALE.get(m["direction"], "1"), scale=m["scale"],
            color=sanitize_color(m["color"], fallback),
        )
        for m in aq["minnows"]
    )
    fish = "\n".join(
        fish_t.substitute(
            x=f["x"], y=f["y"], anim_class=_esc(f["anim_class"]),
            dur_sec=f["dur_sec"], delay_sec=f["delay_sec"],
            dir_scale=FISH_DIR_SCALE.get(f["direction"], "1"), scale=f["scale"],
            tail_dur_sec=f["tail_dur_sec"],
            body_color=sanitize_color(f["body_color"], fallback),
            fin_color=sanitize_color(f["fin_color"], fallback),
            eye_color=sanitize_color(f["eye_color"], theme["bg_start"]),
        )
        for f in aq["fish"]
    )
    bubbles = "\n".join(
        bubble_t.substitute(
            cx=b["cx"], cy=b["cy"], r=b["r"], dur_sec=b["dur_sec"],
            delay_sec=b["delay_sec"], color=sanitize_color(b["color"], fallback),
        )
        for b in aq["bubbles"]
    )
    return {"kelp": kelp, "minnows": minnows, "fish": fish, "bubbles": bubbles}


def render_hero_svg(
    cfg: dict[str, Any],
    breeds_cfg: dict[str, Any],
    templates: dict[str, dict[str, str]],
    *,
    companion_breed_id: str,
    seed: str,
) -> str:
    """Render the animated Cat & Fish Fleet aquarium hero banner."""
    theme, hero, snips = cfg["theme"], cfg["hero"], cfg["svg_snippets"]
    aq = hero["aquarium"]
    mc, cc = aq["main_cat"], aq["companion_cat"]
    wrapper = snips["cat_wrapper"]
    main_cat = render_cat(mc["breed"], mc["pose"], breeds_cfg, wrapper, x=mc["x"], y=mc["y"], scale=mc["scale"], flip=mc["flip"])
    comp_cat = render_cat(companion_breed_id, cc["pose"], breeds_cfg, wrapper, x=cc["x"], y=cc["y"], scale=cc["scale"], flip=cc["flip"])

    hero_tmpl = templates["hero_banner"]
    extra_defs = Template(hero_tmpl["defs"]).substitute(
        tank_clip_id=_esc(aq["clip_id"]), tank_x=aq["x"], tank_y=aq["y"],
        tank_width=aq["width"], tank_height=aq["height"], tank_rx=aq["rx"],
    )
    body = Template(hero_tmpl["body"]).substitute(
        seed=_safe_seed(seed), font_family=_esc(theme["font_family"]),
        text_primary=theme["text_primary"], text_secondary=theme["text_secondary"],
        chip_bg=theme["chip_bg"], chip_border=theme["chip_border"],
        pill_lavender=theme["pill_lavender"], accent_end=theme["accent_end"],
        name=_esc(hero["name"]), handle=_esc(hero["handle"]), role=_esc(hero["role"]),
        tagline_1=_esc(hero["tagline_1"]), tagline_2=_esc(hero["tagline_2"]),
        chips=_render_hero_chips(hero, theme, snips),
        tank_clip_id=_esc(aq["clip_id"]), tank_x=aq["x"], tank_y=aq["y"],
        tank_width=aq["width"], tank_height=aq["height"], tank_rx=aq["rx"],
        main_cat=main_cat, companion_cat=comp_cat,
        **_render_aquarium_parts(aq, theme, snips),
    )
    return render_card_shell(
        cfg, templates, width=int(hero["width"]), height=int(hero["height"]),
        clip_id=hero["clip_id"], title_id=hero["title_id"], desc_id=hero["desc_id"],
        title=hero["title"], desc=hero["desc"], extra_defs=extra_defs, body=body,
    )


def render_catportfolio_svg(
    cfg: dict[str, Any],
    breeds_cfg: dict[str, Any],
    templates: dict[str, dict[str, str]],
) -> str:
    """Render the animated Black Cat CatPortfolio card."""
    theme, card, snips = cfg["theme"], cfg["catportfolio_card"], cfg["svg_snippets"]
    cat_cfg = card["cat"]
    cat_svg = render_cat(
        cat_cfg["breed"], cat_cfg["pose"], breeds_cfg, snips["cat_wrapper"],
        x=cat_cfg["x"], y=cat_cfg["y"], scale=cat_cfg["scale"], flip=cat_cfg["flip"],
    )
    chip_t, bubble_t = Template(snips["cp_chip"]), Template(snips["cp_bubble"])
    chips = "\n".join(
        chip_t.substitute(
            x=c["x"], y=c["y"], width=c["width"], height=c["height"],
            chip_bg=theme["chip_bg"], chip_border=theme["chip_border"],
            text_x=c["text_x"], text_y=c["text_y"],
            font_family=_esc(theme["font_family"]), color="#e8d7ff", text=_esc(c["text"]),
        )
        for c in card["chips"]
    )
    bubbles = "\n".join(
        bubble_t.substitute(
            cx=b["cx"], cy=b["cy"], r=b["r"], opacity=b["opacity"],
            dur_sec=b["dur_sec"], delay_sec=b["delay_sec"],
            color=sanitize_color(b["color"], theme["accent_end"]),
        )
        for b in card["bubbles"]
    )
    cp_tmpl = templates["catportfolio_body"]
    extra_defs = Template(cp_tmpl["defs"]).substitute(
        accent_start=theme["accent_start"], accent_end=theme["accent_end"], pill_cyan=theme["pill_cyan"],
    )
    body = Template(cp_tmpl["body"]).substitute(
        font_family=_esc(theme["font_family"]), bg_start=theme["bg_start"],
        text_primary=theme["text_primary"], text_secondary=theme["text_secondary"],
        accent_start=theme["accent_start"], accent_end=theme["accent_end"],
        pill_lavender=theme["pill_lavender"], pill_cyan=theme["pill_cyan"], title=_esc(card["title"]),
        badge=_esc(card["badge"]), subtitle=_esc(card["subtitle"]),
        url_text=_esc(card["url_text"]), cat=cat_svg, chips=chips, bubbles=bubbles,
    )
    return render_card_shell(
        cfg, templates, width=int(card["width"]), height=int(card["height"]),
        clip_id=card["clip_id"], title_id=card["title_id"], desc_id=card["desc_id"],
        title=card["title"], desc=card["desc"], extra_defs=extra_defs, body=body,
    )


def render_cat_strip_svg(
    cfg: dict[str, Any],
    breeds_cfg: dict[str, Any],
    templates: dict[str, dict[str, str]],
    *,
    roaming_ids: list[str],
    seed: str,
) -> str:
    """Render the animated 3-breed Cat Fleet Strip card."""
    theme, strip, snips = cfg["theme"], cfg["cat_strip"], cfg["svg_snippets"]
    msgs = cfg["messages"]
    wrapper = snips["cat_wrapper"]
    lbl_t = Template(snips["strip_slot_label"])
    cats_out: list[str] = []
    labels_out: list[str] = []
    for slot, bid in zip(strip["slots"], roaming_ids):
        cats_out.append(
            render_cat(bid, slot["pose"], breeds_cfg, wrapper, x=slot["x"], y=slot["y"], scale=slot["scale"], flip=slot["flip"])
        )
        breed_label = str(breeds_cfg["breeds"][bid]["label"])
        caption = msgs["strip_slot_label"].format(label=breed_label, action=slot["action"])
        labels_out.append(
            lbl_t.substitute(
                x=slot["label_x"], y=slot["label_y"],
                font_family=_esc(theme["font_family"]),
                color=theme["text_secondary"], text=_esc(caption),
            )
        )

    strip_tmpl = templates["cat_strip_body"]
    body = Template(strip_tmpl["body"]).substitute(
        seed=_safe_seed(seed), font_family=_esc(theme["font_family"]),
        text_primary=theme["text_primary"], border=theme["border"],
        accent_end=theme["accent_end"], pill_cyan=theme["pill_cyan"],
        title=_esc(strip["title"]), badge=_esc(strip["badge"]),
        cats="\n".join(cats_out), slot_labels="\n".join(labels_out),
    )
    return render_card_shell(
        cfg, templates, width=int(strip["width"]), height=int(strip["height"]),
        clip_id=strip["clip_id"], title_id=strip["title_id"], desc_id=strip["desc_id"],
        title=strip["title"], desc=strip["desc"], extra_defs=strip_tmpl["defs"], body=body,
    )


def _render_stats_rows(metrics: dict[str, Any], cfg: dict[str, Any]) -> str:
    theme, card, snips = cfg["theme"], cfg["stats_card"], cfg["svg_snippets"]
    row_tmpl = Template(snips["stats_row"])
    out: list[str] = []
    for row in card["rows"]:
        y = float(row["y"])
        icon_y = y - float(row["icon_offset_y"])
        color = theme[row["icon_color_key"]]
        icon_elem = ICON_RENDERERS[row["icon_style"]](snips, _esc(row["icon_path"]), color)
        value_str = VALUE_FORMATTERS[row["format_kind"]](metrics[row["key"]], cfg)
        out.append(
            row_tmpl.substitute(
                icon_x=card["icon_x"], icon_y=f"{icon_y:.1f}", icon_scale=row["icon_scale"],
                icon_elem=icon_elem, label_x=card["label_x"], y=int(y),
                font_family=_esc(theme["font_family"]), text_secondary=theme["text_secondary"],
                label=_esc(row["label"]), value_x=card["value_x"],
                text_primary=theme["text_primary"], value=_esc(value_str),
            )
        )
    return "\n".join(out)


def _render_stats_pill(metrics: dict[str, Any], cfg: dict[str, Any]) -> str:
    theme, pill, msgs, snips = cfg["theme"], cfg["stats_card"]["pill"], cfg["messages"], cfg["svg_snippets"]
    streak = int(metrics["current_streak"])
    if streak > 0:
        return Template(snips["streak_pill"]).substitute(
            flame_x=pill["flame_x"], flame_y=pill["flame_y"], flame_scale=pill["flame_scale"],
            flame_path=_esc(pill["flame_path"]), pill_cyan=theme["pill_cyan"],
            text_x=pill["streak_text_x"], text_y=pill["text_y"],
            font_family=_esc(theme["font_family"]), pill_lavender=theme["pill_lavender"],
            label=_esc(msgs["streak_pill"].format(streak=streak)),
        )
    return Template(snips["best_pill"]).substitute(
        text_x=pill["best_text_x"], text_y=pill["text_y"],
        font_family=_esc(theme["font_family"]), pill_lavender=theme["pill_lavender"],
        label=_esc(msgs["best_pill"].format(best=int(metrics["longest_streak"]))),
    )


def _render_spark_bars(weekly_totals: list[int], cfg: dict[str, Any]) -> str:
    spark, snips, chip_bg = cfg["stats_card"]["sparkline"], cfg["svg_snippets"], cfg["theme"]["chip_bg"]
    weeks = int(spark["weeks"])
    padded = ([0] * max(0, weeks - len(weekly_totals))) + weekly_totals[-weeks:]
    max_week = max(padded) if padded else 0
    tmpl = Template(snips["spark_bar"])
    bars: list[str] = []
    for idx, count in enumerate(padded):
        bx = float(spark["start_x"]) + idx * float(spark["step_x"])
        active = count > 0 and max_week > 0
        bh = max(float(spark["min_height"]), (count / max_week) * float(spark["max_height"])) if active else float(spark["min_height"])
        by = float(spark["bottom_y"]) - bh
        bars.append(
            tmpl.substitute(
                x=f"{bx:.1f}", y=f"{by:.1f}", width=spark["bar_width"],
                height=f"{bh:.1f}", rx=spark["rx"], fill="url(#spark)" if active else chip_bg,
            )
        )
    return "\n".join(bars)


def _render_ring_arc(metrics: dict[str, Any], cfg: dict[str, Any]) -> str:
    ring, snips = cfg["stats_card"]["ring"], cfg["svg_snippets"]
    r = float(ring["r"])
    circ = 2.0 * math.pi * r
    frac = min(1.0, max(0.0, float(metrics["active_days"]) / float(cfg["formatting"]["year_days"])))
    if frac <= 0:
        return ""
    return Template(snips["ring_arc"]).substitute(
        cx=ring["cx"], cy=ring["cy"], r=int(r), stroke_width=ring["stroke_width"],
        dash_len=f"{frac * circ:.2f}", circumference=f"{circ:.2f}",
    )


def _render_stats_defs(cfg: dict[str, Any], defs_tmpl: str) -> str:
    theme, card = cfg["theme"], cfg["stats_card"]
    ring, spark = card["ring"], card["sparkline"]
    r = float(ring["r"])
    return Template(defs_tmpl).substitute(
        ring_x1=int(ring["cx"] - r), ring_y1=int(ring["cy"] - r),
        ring_x2=int(ring["cx"] + r), ring_y2=int(ring["cy"] + r),
        accent_start=theme["accent_start"], accent_end=theme["accent_end"],
        spark_y1=int(float(spark["bottom_y"]) - float(spark["max_height"])),
        spark_y2=int(spark["bottom_y"]),
    )


def render_stats_svg(
    metrics: dict[str, Any],
    cfg: dict[str, Any],
    templates: dict[str, dict[str, str]],
    *,
    includes_private: bool,
    updated_date: str,
) -> str:
    """Render the GitHub Activity card SVG from derived metrics."""
    theme, card, msgs = cfg["theme"], cfg["stats_card"], cfg["messages"]
    ring, stats_tmpl = card["ring"], templates["stats_body"]
    footer_msg = msgs[STATS_FOOTER_KEYS[includes_private]].format(
        repos_count=int(metrics["repos_count"]), updated_date=updated_date,
    )
    desc_msg = msgs["stats_desc"].format(
        contribs_12m=f"{int(metrics['contribs_12m']):,}",
        all_time=f"{int(metrics['all_time_contribs']):,}",
        prs=f"{int(metrics['prs_total']):,}",
        active_days=int(metrics["active_days"]),
    )
    body = Template(stats_tmpl["body"]).substitute(
        font_family=_esc(theme["font_family"]), text_primary=theme["text_primary"],
        text_secondary=theme["text_secondary"], text_muted=theme["text_muted"],
        chip_bg=theme["chip_bg"], accent_start=theme["accent_start"],
        title=_esc(card["title"]), pill_inner=_render_stats_pill(metrics, cfg),
        rows=_render_stats_rows(metrics, cfg), ring_cx=ring["cx"], ring_cy=ring["cy"],
        ring_r=int(float(ring["r"])), ring_stroke_width=ring["stroke_width"],
        ring_arc=_render_ring_arc(metrics, cfg),
        ring_value=_esc(format_compact(int(metrics["contribs_12m"]), cfg["formatting"])),
        ring_sublabel=_esc(ring["sublabel"]), spark_bars=_render_spark_bars(metrics["weekly_totals"], cfg),
        footer_x=card["footer_x"], footer_y=card["footer_y"], footer=_esc(footer_msg),
    )
    return render_card_shell(
        cfg, templates, width=int(card["width"]), height=int(card["height"]),
        clip_id=card["clip_id"], title_id=card["title_id"], desc_id=card["desc_id"],
        title=card["title"], desc=desc_msg,
        extra_defs=_render_stats_defs(cfg, stats_tmpl["defs"]), body=body,
    )


def _render_bar_segments(langs: list[dict[str, Any]], cfg: dict[str, Any]) -> str:
    bar = cfg["langs_card"]["bar"]
    default_color = cfg["theme"]["default_lang_color"]
    widths = allocate_bar_widths(langs, float(bar["width"]), float(bar["min_segment_width"]))
    tmpl = Template(cfg["svg_snippets"]["lang_segment"])
    segments: list[str] = []
    cur_x = float(bar["x"])
    for lang, seg_w in zip(langs, widths):
        segments.append(
            tmpl.substitute(
                x=f"{cur_x:.2f}", y=bar["y"], width=f"{seg_w:.2f}",
                height=bar["height"], color=sanitize_color(str(lang["color"]), default_color),
            )
        )
        cur_x += seg_w
    return "\n".join(segments)


def _render_lang_legend(langs: list[dict[str, Any]], cfg: dict[str, Any]) -> str:
    theme, legend, fmt = cfg["theme"], cfg["langs_card"]["legend"], cfg["formatting"]
    max_len, ellipsis, dec = int(fmt["max_lang_name_len"]), str(fmt["ellipsis"]), int(fmt["percent_decimals"])
    rows_per_col = int(legend["rows_per_col"])
    tmpl = Template(cfg["svg_snippets"]["lang_legend_item"])
    items: list[str] = []
    for idx, lang in enumerate(langs[: int(cfg["langs_card"]["limit"])]):
        col_cfg = legend["cols"][idx // rows_per_col]
        y = int(legend["row_ys"][idx % rows_per_col])
        raw_name = str(lang["name"])
        display_name = raw_name if len(raw_name) <= max_len else f"{raw_name[: max_len - 1]}{ellipsis}"
        items.append(
            tmpl.substitute(
                cx=col_cfg["cx"], cy=y - int(legend["dot_offset_y"]), dot_r=legend["dot_r"],
                color=sanitize_color(str(lang["color"]), theme["default_lang_color"]),
                name_x=col_cfg["name_x"], y=y, font_family=_esc(theme["font_family"]),
                text_primary=theme["text_primary"], name=_esc(display_name),
                pct_x=col_cfg["pct_x"], text_secondary=theme["text_secondary"],
                pct=_esc(f"{float(lang['pct']):.{dec}f}%"),
            )
        )
    return "\n".join(items)


def render_langs_svg(
    metrics: dict[str, Any],
    cfg: dict[str, Any],
    templates: dict[str, dict[str, str]],
    *,
    includes_private: bool,
    has_exclusions: bool,
) -> str:
    """Render the Top Languages card SVG from derived metrics."""
    theme, card, msgs = cfg["theme"], cfg["langs_card"], cfg["messages"]
    langs, bar, langs_tmpl = metrics["top_langs"], card["bar"], templates["top_langs_body"]
    extra_defs = Template(langs_tmpl["defs"]).substitute(
        bar_x=int(bar["x"]), bar_y=bar["y"], bar_width=int(bar["width"]),
        bar_height=bar["height"], bar_rx=bar["rx"],
    )
    footer_msg = msgs[LANGS_FOOTER_KEYS[(includes_private, has_exclusions)]].format(
        repos_count=int(metrics["repos_count"]),
    )
    dec = int(cfg["formatting"]["percent_decimals"])
    desc = ", ".join(f"{item['name']} {float(item['pct']):.{dec}f}%" for item in langs) or msgs["no_langs_desc"]
    body = Template(langs_tmpl["body"]).substitute(
        font_family=_esc(theme["font_family"]), text_primary=theme["text_primary"],
        text_muted=theme["text_muted"], accent_end=theme["accent_end"],
        pill_cyan=theme["pill_cyan"], chip_bg=theme["chip_bg"],
        title=_esc(card["title"]), badge=_esc(card["badge"]),
        bar_x=int(bar["x"]), bar_y=bar["y"], bar_width=int(bar["width"]),
        bar_height=bar["height"], bar_rx=bar["rx"],
        bar_segments=_render_bar_segments(langs, cfg),
        legend_items=_render_lang_legend(langs, cfg),
        footer_x=card["footer_x"], footer_y=card["footer_y"], footer=_esc(footer_msg),
    )
    return render_card_shell(
        cfg, templates, width=int(card["width"]), height=int(card["height"]),
        clip_id=card["clip_id"], title_id=card["title_id"], desc_id=card["desc_id"],
        title=card["title"], desc=desc, extra_defs=extra_defs, body=body,
    )


CARD_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "hero": lambda ctx: render_hero_svg(
        ctx["cfg"], ctx["breeds"], ctx["templates"],
        companion_breed_id=ctx["companion_breed_id"], seed=ctx["seed"],
    ),
    "catportfolio": lambda ctx: render_catportfolio_svg(ctx["cfg"], ctx["breeds"], ctx["templates"]),
    "cat_strip": lambda ctx: render_cat_strip_svg(
        ctx["cfg"], ctx["breeds"], ctx["templates"],
        roaming_ids=ctx["strip_breed_ids"], seed=ctx["seed"],
    ),
    "stats": lambda ctx: render_stats_svg(
        ctx["metrics"], ctx["cfg"], ctx["templates"],
        includes_private=ctx["include_private"], updated_date=ctx["updated_date"],
    ),
    "top_langs": lambda ctx: render_langs_svg(
        ctx["metrics"], ctx["cfg"], ctx["templates"],
        includes_private=ctx["include_private"], has_exclusions=ctx["has_exclusions"],
    ),
}
