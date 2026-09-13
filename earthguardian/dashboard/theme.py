"""The 2024 design, rebuilt.

The thesis contains one screen the author spent three weeks on - Figura 14,
"Dashboard EarthGuardian" - and the design outlived the code. This module is
that screen: the palette is sampled from the figure itself, the typeface is Work
Sans, chosen to match its lettering, the six navigation items are the ones it
lists, and every page keeps its layout.

Where the original showed a placeholder, this shows the real thing. The progress
cards are the actual crops at their actual stage, the day column is what the
node's light sensor saw, and the insight panel says whatever GAIA concluded
rather than always "It's the perfect day for spraying".

What is not reproduced is the illustration work - the mango, the pear, the
avocado with faces. Line-art marks stand in for them, which is more honest than
clip art pretending to be the originals.
"""

from __future__ import annotations

# --- sampled from the figure ------------------------------------------------------------
FOREST = "#1a4c46"  # sidebar and dark cards
FOREST_SOFT = "#305d49"  # the dark weather card
MOSS = "#5f8775"  # the news card
LIME = "#d4f0a1"  # the active menu pill, callouts, the soil block
CREAM = "#f7ffe1"  # pale cards
SAGE = "#e6f0e1"  # panels and the metric strip
PAPER = "#fefcfd"  # page background
LINE = "#e5e8e7"  # borders
MUTED = "#7d9a96"  # secondary text
INK = "#12332e"  # body text

# --- what the figure never had to draw --------------------------------------------------
LEAF = "#8db14a"  # a lime dark enough to draw a line with on a pale ground
SLATE = "#a7bab4"  # the series that is not the point
AMBER = "#c98a1e"
ALARM = "#c2453d"
GRID = "#d5e2d8"

FONT = "Work Sans"

#: The navigation the original screen listed, in its order.
NAV = ["Overview", "Fields", "Sensors", "Analytics", "Reports", "Devices"]

SEVERITY_COLOURS = {"critical": ALARM, "high": AMBER, "medium": MOSS, "info": MUTED}

_SEARCH_ICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' "
    "viewBox='0 0 24 24' fill='none' stroke='%237d9a96' stroke-width='2.2' "
    "stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='6.5'/%3E"
    "%3Cpath d='m16 16 4.5 4.5'/%3E%3C/svg%3E"
)

_TOKENS = f"""
:root {{
  --eg-forest: {FOREST}; --eg-forest-soft: {FOREST_SOFT}; --eg-moss: {MOSS};
  --eg-lime: {LIME}; --eg-cream: {CREAM}; --eg-sage: {SAGE}; --eg-paper: {PAPER};
  --eg-line: {LINE}; --eg-muted: {MUTED}; --eg-ink: {INK};
  --eg-search: url("{_SEARCH_ICON}");
}}
"""

_RULES = """
/* ---- canvas ---- */
.stApp { background: var(--eg-paper); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stDecoration"], [data-testid="stAppDeployButton"], [data-testid="stMainMenu"],
[data-testid="stElementToolbar"] { display: none; }
[data-testid="stMainBlockContainer"] { padding: 2.2rem 2.6rem 3rem 2.6rem; max-width: 1480px; }
/* Markdown pulls the next element up by a paragraph margin; these blocks have no paragraph. */
[data-testid="stMarkdownContainer"]:has(> [class^="eg-"]) { margin-bottom: 0; }

/* ---- sidebar: the dark green panel ---- */
[data-testid="stSidebar"] {
  background: var(--eg-forest); color: var(--eg-cream);
  width: 250px !important; min-width: 250px !important; max-width: 250px !important;
}
[data-testid="stSidebarContent"] { padding: 0; }
[data-testid="stSidebarUserContent"] { padding: 0 1.1rem 1.6rem 1.1rem; }
/* the news card and the footer sit at the bottom of the panel, as the figure's did */
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] { min-height: calc(100vh - 6.5rem); }
[data-testid="stLayoutWrapper"]:has(> .st-key-eg-sidebar-foot) { margin-top: auto; }
.eg-wordmark { font-size: 1.5rem; font-weight: 300; letter-spacing: .01em; color: #fff; margin: 0 0 1.4rem 0; }
/* the menu radio, restyled into the pill list of the original */
.st-key-page, .st-key-page [data-testid="stRadio"], .st-key-page [data-testid="stRadioGroup"] { width: 100%; }
.st-key-page [data-testid="stRadioGroup"] { gap: .95rem; }
.st-key-page [data-testid="stRadioOption"] {
  width: 150px; margin: 0; padding: .5rem 1.35rem; border-radius: 6px; cursor: pointer;
}
/* the radio circle */
.st-key-page [data-testid="stRadioOption"] > div > div > div:first-child { display: none; }
.st-key-page [data-testid="stRadioOption"] p { font-size: .94rem; font-weight: 700; color: #fff; }
.st-key-page [data-testid="stRadioOption"]:hover p { color: var(--eg-lime); }
.st-key-page [data-testid="stRadioOption"]:has(input:checked) { background: var(--eg-lime); }
.st-key-page [data-testid="stRadioOption"]:has(input:checked) p { color: var(--eg-forest); }
.eg-news {
  position: relative; overflow: hidden; background: var(--eg-moss); border-radius: 8px;
  padding: .85rem 1rem 3.4rem 1rem;
}
.eg-news .label { font-size: .72rem; color: #c6d8d1; margin-bottom: .35rem; }
.eg-news .text { position: relative; z-index: 1; font-size: .84rem; line-height: 1.38; font-weight: 500; color: #fff; }
.eg-news .art { position: absolute; right: -10px; bottom: -14px; line-height: 0; opacity: .85; }
.eg-foot { margin-top: 1.2rem; font-size: .94rem; font-weight: 700; color: #fff; }
.eg-foot span { margin-left: .4rem; font-size: .74rem; font-weight: 400; color: #9fbdb4; }

/* ---- top bar ---- */
.eg-tag {
  display: inline-block; background: var(--eg-lime); color: var(--eg-forest); border-radius: 999px;
  padding: .55rem 1.2rem; font-size: .8rem; font-weight: 600; white-space: nowrap;
}
.st-key-field .react-aria-ComboBox > div, .st-key-field [data-baseweb="select"] > div {
  background: #fff var(--eg-search) no-repeat .8rem 50%; padding-left: 1.9rem; min-height: 2.6rem;
  border: 1.5px solid #a9c1ba; border-radius: 4px;
}
.st-key-field input, .st-key-field [data-baseweb="select"] > div * {
  font-size: .85rem; font-weight: 600; color: var(--eg-ink);
}
.eg-user { display: flex; align-items: center; gap: .85rem; }
.eg-avatar {
  flex: none; width: 44px; height: 44px; border-radius: 10px; background: var(--eg-lime);
  color: var(--eg-forest); display: flex; align-items: center; justify-content: center;
  font-size: .9rem; font-weight: 700; letter-spacing: .04em;
}
.eg-user b { display: block; font-size: 1.02rem; font-weight: 700; color: var(--eg-ink); line-height: 1.2; }
.eg-user span { display: block; font-size: .74rem; color: var(--eg-muted); }

/* ---- section heading ---- */
.eg-h { display: flex; align-items: center; gap: .6rem; margin: .55rem 0 0 0;
  font-size: 1.12rem; font-weight: 600; color: var(--eg-ink); }

/* ---- the row of cards ---- */
.eg-cards { display: grid; grid-template-columns: repeat(var(--eg-n), minmax(0, 1fr)); gap: 1rem; }
.eg-card {
  height: 178px; border-radius: 10px; padding: .9rem .7rem 1rem .7rem;
  display: flex; flex-direction: column; align-items: center; text-align: center;
}
.eg-card .art { flex: 1; display: flex; align-items: center; justify-content: center; }
.eg-card .name, .eg-card .value { font-size: .84rem; line-height: 1.25; }
.eg-card .value { margin-top: .4rem; }
.eg-cards.compact .eg-card { height: 148px; padding: .8rem .45rem .85rem .45rem; }
.eg-cards.compact .eg-card .name, .eg-cards.compact .eg-card .value { font-size: .76rem; }
.eg-card.dark { background: var(--eg-forest); color: #8fb2a8; }
.eg-card.pale { background: var(--eg-cream); color: #97aea7; }
.eg-card.focus .value { font-weight: 700; }
.eg-card.dark.focus .value { color: #fff; }
.eg-card.pale.focus .value { color: var(--eg-forest); }

/* ---- panels ---- */
[class*="st-key-eg-panel"] {
  background: var(--eg-sage); border-radius: 12px; padding: 1.25rem 1.4rem 1.2rem 1.4rem;
}
/* the analysis and insight panels share a height, as the figure's do */
[data-testid="stColumn"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(
  > .st-key-eg-panel-lead, > .st-key-eg-panel-insight, > .st-key-eg-panel-brief) { flex: 1 1 auto; }
[data-testid="stLayoutWrapper"] > :is(.st-key-eg-panel-lead, .st-key-eg-panel-insight, .st-key-eg-panel-brief) {
  height: 100%;
}
.st-key-eg-panel-lead { position: relative; overflow: visible; }
.st-key-eg-panel-lead::before {
  content: ""; position: absolute; left: -9px; top: 1.45rem; border-style: solid;
  border-width: 9px 9px 9px 0; border-color: transparent var(--eg-sage) transparent transparent;
}
.eg-panel-title { font-size: 1.08rem; font-weight: 700; color: var(--eg-ink); line-height: 1.3; }
[class*="st-key-eg-panel"] [data-testid="stDownloadButton"] button {
  background: var(--eg-forest); color: var(--eg-cream); border: none; border-radius: 6px;
}
[class*="st-key-eg-panel"] [data-testid="stDownloadButton"] button:hover { background: var(--eg-moss); color: #fff; }
.st-key-channel [data-testid="stButtonGroup"] button { min-height: 1.8rem; padding: .1rem .6rem; }
.st-key-channel [data-testid="stButtonGroup"] button p { font-size: .76rem; }

/* ---- the insight panel: a sentence on top, the illustration and its numbers below ---- */
.st-key-eg-panel-insight { justify-content: space-between; }
.eg-insight-title { max-width: 17rem; }
.eg-insight-body { display: grid; grid-template-columns: 1fr 1fr; gap: 1.1rem; align-items: end; }
.eg-insight-art svg { display: block; width: 100%; height: auto; }
.eg-readout { font-size: .76rem; color: var(--eg-muted); line-height: 1.35; margin-bottom: .85rem; }
.eg-readout:last-child { margin-bottom: 0; }
.eg-readout b { display: block; font-size: .88rem; font-weight: 500; color: #4f6f67; }
.st-key-eg-panel-insight .eg-note { margin-top: .95rem; }
.eg-block { background: var(--eg-lime); aspect-ratio: 1 / 1.1; display: flex; align-items: center; justify-content: center; }

/* ---- metric strip ---- */
.eg-strip {
  background: var(--eg-sage); border-radius: 12px; padding: .95rem 1.5rem;
  display: flex; justify-content: space-between; gap: 1.2rem;
}
.eg-metric { display: flex; align-items: center; gap: .85rem; min-width: 0; }
.eg-metric svg { flex: none; }
.eg-metric .label { font-size: .76rem; color: var(--eg-muted); }
.eg-metric .value { font-size: .82rem; font-weight: 600; color: #4f6f67; }

/* ---- the column down the right ---- */
.eg-side { display: flex; flex-direction: column; gap: 1.1rem; }
.eg-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: 1.1rem; align-items: center; }
.eg-big { text-align: center; padding: .1rem 0 .75rem 0; border-bottom: 3px solid transparent; }
.eg-big.strong { border-bottom-color: var(--eg-forest); }
.eg-big.soft { border-bottom-color: var(--eg-line); }
.eg-big .n { font-size: 2.15rem; font-weight: 400; color: var(--eg-ink); line-height: 1.15; white-space: nowrap; }
.eg-big .m { font-size: .72rem; color: var(--eg-muted); margin-top: .1rem; }
.eg-tile { position: relative; overflow: hidden; border-radius: 10px; padding: .75rem .85rem; height: 104px; }
.eg-tile.tall { height: auto; min-height: 104px; }
.eg-tile.dark { background: var(--eg-forest-soft); }
.eg-tile.pale { background: var(--eg-cream); }
.eg-tile .t, .eg-tile .s { position: relative; z-index: 1; }
.eg-tile .t { font-size: .95rem; font-weight: 700; line-height: 1.25; }
.eg-tile .s { font-size: .72rem; line-height: 1.3; margin-top: .25rem; }
.eg-tile.dark .t, .eg-tile.dark .s { color: var(--eg-lime); }
.eg-tile.dark .s { font-weight: 600; }
.eg-tile.pale .t { color: var(--eg-forest); }
.eg-tile.pale .s { color: var(--eg-muted); }
.eg-tile .art { position: absolute; right: -10px; bottom: -12px; line-height: 0; }
.eg-tile .art svg { width: 64px; height: 64px; }

/* ---- small print ---- */
.eg-note { color: #5f7d75; font-size: .78rem; line-height: 1.5; }
.eg-pill {
  display: inline-block; padding: .1rem .55rem; border-radius: 999px;
  font-size: .64rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
}
.eg-brief {
  font-size: .74rem; line-height: 1.5; color: #4f6f67; max-height: 9.6rem; overflow: hidden;
  -webkit-mask-image: linear-gradient(#000 55%, transparent); mask-image: linear-gradient(#000 55%, transparent);
}
.eg-brief b { color: var(--eg-ink); }
"""

CSS = f"<style>{_TOKENS}{_RULES}</style>"

# --- line-art marks, in the register of the original's weather icons ---------------------

CHEVRON = (
    f'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{INK}" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="m6 9 6 6 6-6"/></svg>'
)

#: 24-unit icons, drawn to one stroke weight whatever size they are shown at.
ICONS: dict[str, str] = {
    "air": '<path d="M3 9h10a3 3 0 1 0-3-3"/><path d="M3 14h15a3 3 0 1 1-3 3"/><path d="M3 19h6"/>',
    "drop": '<path d="M12 3.5s-6 6.8-6 11a6 6 0 0 0 12 0c0-4.2-6-11-6-11z"/>',
    "humidity": '<path d="M12 3.5s-6 6.8-6 11a6 6 0 0 0 12 0c0-4.2-6-11-6-11z"/>'
    '<path d="M9.3 14.6a2.7 2.7 0 0 0 2.7 2.7"/>',
    "sprout": '<path d="M3 20.5h18"/><path d="M12 20.5v-9"/>'
    '<path d="M12 13.5c-4 0-6.5-2.5-6.5-6 4 0 6.5 2.5 6.5 6z"/>'
    '<path d="M12 11.5c0-3.5 2.5-6 6.5-6 0 3.5-2.5 6-6.5 6z"/>',
    "leaf": '<path d="M5 19c0-8 5-13 14-14-1 9-6 14-14 14z"/><path d="M5 19l7-7"/>',
    "thermometer": '<path d="M10 13.5V5a2 2 0 1 1 4 0v8.5a4 4 0 1 1-4 0z"/><path d="M12 9v7.5"/>',
    "sun": '<circle cx="12" cy="12" r="4"/>'
    '<path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4'
    'M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"/>',
    "flask": '<path d="M9.5 3h5"/><path d="M10.5 3v5.5L5.2 18a2 2 0 0 0 1.8 3h10a2 2 0 0 0 '
    '1.8-3l-5.3-9.5V3"/><path d="M7.6 14.5h8.8"/>',
    "battery": '<rect x="2.5" y="7" width="17" height="10" rx="2"/><path d="M21.5 11v2"/>'
    '<path d="M6 10.5v3M9.5 10.5v3M13 10.5v3"/>',
    "antenna": '<path d="M12 11v10"/><path d="M8.5 21h7"/><circle cx="12" cy="9" r="1.8"/>'
    '<path d="M8.2 5.2a5.4 5.4 0 0 0 0 7.6M15.8 5.2a5.4 5.4 0 0 1 0 7.6"/>'
    '<path d="M5.3 2.5a9.2 9.2 0 0 0 0 13M18.7 2.5a9.2 9.2 0 0 1 0 13"/>',
    "coin": '<circle cx="12" cy="12" r="8.5"/><path d="M14.6 9.2c-.5-.9-1.5-1.4-2.6-1.4-1.5 0-2.6.8'
    '-2.6 2s1.1 1.7 2.6 2.1 2.6.9 2.6 2.1-1.1 2-2.6 2c-1.1 0-2.1-.5-2.6-1.4"/>'
    '<path d="M12 6.2v1.6M12 16.2v1.6"/>',
    "hinge": '<path d="M3 18h8.5L20.5 6"/><circle cx="11.5" cy="18" r="1.7"/>'
    '<path d="M3 21.5h18" stroke-dasharray="1.5 2.5"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/>'
    '<circle cx="12" cy="12" r=".8"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/>'
    '<path d="M3.5 10h17M8.5 3v4M15.5 3v4"/>',
    "report": '<path d="M6 3h8.5L19 7.5V21H6z"/><path d="M14 3v5h5"/><path d="M9 13h7M9 17h5"/>',
}


def _svg(paths: str, view: int, colour: str, size: int, stroke_px: float) -> str:
    stroke = stroke_px * view / size
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {view} {view}" fill="none" '
        f'stroke="{colour}" stroke-width="{stroke:.2f}" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )


def icon(name: str, colour: str, size: int = 28, stroke_px: float = 1.7) -> str:
    return _svg(ICONS[name], 24, colour, size, stroke_px)


def icon_block(name: str) -> str:
    """An icon on the lime block the original's insight illustration stood on."""
    return f'<div class="eg-block">{icon(name, FOREST, 64)}</div>'


#: 62-unit crop marks.
CROPS: dict[str, str] = {
    "Lettuce": '<path d="M31 52c-11 0-19-7-19-16 0-3 1-6 3-8"/>'
    '<path d="M31 52c11 0 19-7 19-16 0-3-1-6-3-8"/>'
    '<path d="M15 28c0-9 7-16 16-16s16 7 16 16"/>'
    '<path d="M31 12v40M22 20c3 6 4 14 3 24M40 20c-3 6-4 14-3 24"/>',
    "Coffee": '<path d="M31 54V22"/>'
    '<path d="M31 34c-8 0-14-5-14-12 8 0 14 5 14 12z"/>'
    '<path d="M31 34c8 0 14-5 14-12-8 0-14 5-14 12z"/>'
    '<path d="M31 46c-7 0-12-4-12-10 7 0 12 4 12 10z"/>'
    '<path d="M31 46c7 0 12-4 12-10-7 0-12 4-12 10z"/>'
    '<circle cx="31" cy="14" r="5"/>',
    "Tomato": '<circle cx="31" cy="36" r="17"/><path d="M31 19v-6"/>'
    '<path d="M31 19l-9-4M31 19l9-4M31 19l-5 6M31 19l5 6"/>'
    '<path d="M24 31c-2 2-3 5-3 8"/>',
}


def crop_mark(crop: str, colour: str, size: int = 64) -> str:
    return _svg(CROPS.get(crop, CROPS["Lettuce"]), 62, colour, size, 2.0)


#: 42-unit weather marks.
WEATHER: dict[str, str] = {
    "sunny": '<circle cx="21" cy="21" r="8"/>'
    '<path d="M21 4v5M21 33v5M4 21h5M33 21h5M9 9l3.5 3.5M29.5 29.5L33 33M33 9l-3.5 3.5'
    'M12.5 29.5L9 33"/>',
    "partly": '<circle cx="16" cy="16" r="6"/><path d="M16 4v4M4 16h4M8 8l2.5 2.5M24 8l-2.5 2.5"/>'
    '<path d="M14 32h16a6 6 0 000-12 8 8 0 00-15 2 5 5 0 00-1 10z"/>',
    "cloudy": '<path d="M12 30h18a7 7 0 000-14 9 9 0 00-17 2 6 6 0 00-1 12z"/><path d="M9 34h22"/>',
}


def weather_mark(condition: str, colour: str, size: int = 64) -> str:
    return _svg(WEATHER.get(condition, WEATHER["sunny"]), 42, colour, size, 1.6)


def plant_mark(level: float) -> str:
    """The seedling over its root zone, from the original's insight panel.

    The original filled the soil block solid; here the lime is the water the
    crop can still use, so the block empties as the plot dries.
    """
    level = min(max(level, 0.0), 1.0)
    surface, depth = 62.0, 108.0
    water = surface + depth * (1.0 - level)
    return (
        f'<svg viewBox="0 0 140 170" fill="none" stroke="{FOREST}" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'<rect x="0" y="{surface}" width="140" height="{depth}" fill="{CREAM}" stroke="none"/>'
        f'<rect x="0" y="{water:.1f}" width="140" height="{surface + depth - water:.1f}" '
        f'fill="{LIME}" stroke="none"/>'
        f'<path d="M0 {surface}h140" stroke-width="2"/>'
        '<path d="M70 62c0-12-3-20 1-31"/>'
        '<path d="M71 31c2-9 11-11 13-4 1 5-5 7-7 3"/>'
        '<path d="M70 47c-10 0-17-6-17-14 9-1 16 5 17 14z"/>'
        '<path d="M71 40c6-7 16-8 21-3-6 7-15 8-21 3z"/>'
        '<path d="M70 62c0 18 1 33-1 50"/>'
        '<path d="M70 72c-8 4-16 10-24 22M70 76c9 5 17 11 25 23"/>'
        '<path d="M70 90c-7 6-12 14-18 26M70 94c7 6 12 14 18 26"/>'
        '<path d="M69 112c-3 8-5 16-9 24M69 112c4 8 7 16 11 22"/>'
        '<path d="M46 94l-8 3M52 83l-3-6M95 99l9 1M86 87l4-6M52 116l-8 2M88 120l7 4"/>'
        "</svg>"
    )
