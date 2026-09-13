"""Components and charts in the 2024 design.

Everything on a page that is not a Streamlit widget is built here, as markup
against the classes in :mod:`earthguardian.dashboard.theme`, so the pages read
as layout and data rather than as HTML.

Chart styling is applied last: Altair refuses to layer a chart that already
carries a ``config``, so every helper that might end up inside a layer returns
an unstyled chart and :func:`style` finishes the composition.
"""

from __future__ import annotations

import html as markup
from dataclasses import dataclass

import altair as alt
import pandas as pd
import streamlit as st

from earthguardian.dashboard import theme

#: One colour per plot, in fleet order.
PLOT_COLOURS = [theme.FOREST, theme.LEAF, theme.SLATE]
#: Rainfed, fixed calendar, sensor-driven.
POLICY_COLOURS = [theme.SLATE, theme.LEAF, theme.FOREST]


# --------------------------------------------------------------------------------------
# Markup
# --------------------------------------------------------------------------------------


def apply_theme() -> None:
    st.markdown(theme.CSS, unsafe_allow_html=True)


def html(fragment: str) -> None:
    """Render markup with its whitespace collapsed - Markdown reads an indented line as code."""
    st.markdown(" ".join(fragment.split()), unsafe_allow_html=True)


def escape(value: object) -> str:
    """Escape for HTML, and for Markdown's maths: two prices on one line read as a formula."""
    return markup.escape(str(value)).replace("$", "&#36;")


def brl(value: float) -> str:
    return f"R$ {value:,.0f}"


def wordmark(name: str) -> None:
    html(f'<div class="eg-wordmark">{escape(name)}</div>')


def news(label: str, body: str) -> None:
    html(
        f'<div class="eg-news"><div class="label">{escape(label)}</div>'
        f'<div class="text">{escape(body)}</div>'
        f'<div class="art">{theme.icon("sprout", theme.LIME, 78)}</div></div>'
    )


def footer(label: str, detail: str) -> None:
    html(f'<div class="eg-foot">{escape(label)}<span>{escape(detail)}</span></div>')


def tag(label: str) -> None:
    html(f'<div class="eg-tag">{escape(label)}</div>')


def user(initials: str, name: str, detail: str) -> None:
    html(
        f'<div class="eg-user"><div class="eg-avatar">{escape(initials)}</div>'
        f"<div><b>{escape(name)}</b><span>{escape(detail)}</span></div></div>"
    )


def heading(label: str, *, chevron: bool = False) -> None:
    mark = theme.CHEVRON if chevron else ""
    html(f'<div class="eg-h">{escape(label)}{mark}</div>')


def panel_title(label: str) -> None:
    html(f'<div class="eg-panel-title">{escape(label)}</div>')


def note(body: str) -> None:
    html(f'<div class="eg-note">{escape(body)}</div>')


def briefing(body: str, lines: int = 8) -> None:
    """The first lines of a plain-text briefing; the rest is in the download."""
    head, *rest = body.splitlines()[:lines]
    text = "<br>".join([f"<b>{escape(head)}</b>", *(escape(line.strip()) for line in rest)])
    html(f'<div class="eg-brief">{text}</div>')


@dataclass(frozen=True, slots=True)
class Card:
    """One of the tiles along the top of a page."""

    art: str
    name: str
    value: str
    dark: bool = False
    #: The card the page is about: its value is set in bold, as the figure's avocado was.
    focus: bool = False


def cards(items: list[Card], *, compact: bool = False) -> None:
    body = "".join(
        f'<div class="eg-card {"dark" if card.dark else "pale"}{" focus" if card.focus else ""}">'
        f'<div class="art">{card.art}</div>'
        f'<div class="name">{escape(card.name)}</div>'
        f'<div class="value">{escape(card.value)}</div></div>'
        for card in items
    )
    grid = "eg-cards compact" if compact else "eg-cards"
    html(f'<div class="{grid}" style="--eg-n: {len(items)}">{body}</div>')


def insight(
    title: str, art: str, readouts: list[tuple[str, str]], footnote: str | None = None
) -> None:
    """The panel beside the analysis: a sentence, an illustration and the numbers behind it."""
    rows = "".join(
        f'<div class="eg-readout"><b>{escape(value)}</b>{escape(label)}</div>'
        for value, label in readouts
    )
    foot = f'<div class="eg-note">{escape(footnote)}</div>' if footnote else ""
    # A container rather than one block of markup, so the panel can stretch to
    # the height of the analysis beside it and keep its illustration at the bottom.
    with st.container(key="eg-panel-insight"):
        html(f'<div class="eg-panel-title eg-insight-title">{escape(title)}</div>')
        html(
            f'<div class="eg-insight-body"><div class="eg-insight-art">{art}</div>'
            f"<div>{rows}</div></div>{foot}"
        )


def strip(metrics: list[tuple[str, str, str]]) -> None:
    """The band along the bottom: ``(icon, label, value)`` for each metric."""
    items = "".join(
        f'<div class="eg-metric">{theme.icon(name, theme.MUTED, 34)}'
        f'<div><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div></div></div>'
        for name, label, value in metrics
    )
    html(f'<div class="eg-strip">{items}</div>')


@dataclass(frozen=True, slots=True)
class SideItem:
    """A row of the column down the right: a big figure beside a tile."""

    big: str
    small: str
    title: str
    detail: str
    art: str = ""
    dark: bool = False
    #: Draw ``detail`` as a pill in this colour.
    pill_colour: str | None = None


#: The rules under the figure's day numbers: two strong, one faint, then none.
_RULES = ("strong", "strong", "soft")


def side_list(items: list[SideItem], *, tall: bool = False) -> None:
    rows = []
    for index, item in enumerate(items):
        rule = _RULES[index] if index < len(_RULES) else ""
        detail = escape(item.detail)
        if item.pill_colour:
            detail = (
                f'<span class="eg-pill" style="background: {item.pill_colour}2e; '
                f'color: {item.pill_colour}">{detail}</span>'
            )
        tile = f"eg-tile {'dark' if item.dark else 'pale'}{' tall' if tall else ''}"
        rows.append(
            f'<div class="eg-row"><div class="eg-big {rule}">'
            f'<div class="n">{escape(item.big)}</div><div class="m">{escape(item.small)}</div></div>'
            f'<div class="{tile}"><div class="t">{escape(item.title)}</div>'
            f'<div class="s">{detail}</div><div class="art">{item.art}</div></div></div>'
        )
    html(f'<div class="eg-side">{"".join(rows)}</div>')


# --------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------


def chart(spec: alt.TopLevelMixin) -> None:
    st.altair_chart(spec, theme=None, width="stretch")


def style(chart: alt.Chart | alt.LayerChart, height: int = 236) -> alt.Chart | alt.LayerChart:
    return (
        chart.properties(height=height)
        .configure(font=theme.FONT, background="transparent")
        .configure_view(strokeWidth=0)
        .configure_axis(
            grid=True,
            gridColor=theme.GRID,
            domain=False,
            ticks=False,
            labelColor=theme.MUTED,
            titleColor=theme.MUTED,
            labelFontSize=10,
            titleFontSize=10,
            titleFontWeight="normal",
            labelPadding=6,
        )
        .configure_legend(
            labelColor="#4f6f67",
            titleColor=theme.MUTED,
            orient="top",
            direction="horizontal",
            labelFontSize=11,
            symbolStrokeWidth=2.5,
        )
        .configure_title(color=theme.INK, fontSize=12, anchor="start")
    )


def outlook_chart(
    observed: pd.DataFrame,
    projected: pd.DataFrame,
    *,
    order: list[str],
    focus: str,
    callout: str,
    trigger: float,
    height: int = 236,
) -> alt.LayerChart:
    """Every plot's reserve, where it is heading, and the lime callout the figure had."""
    start = pd.Timestamp(observed["date"].min())
    stop = pd.Timestamp(projected["date"].max()) + pd.Timedelta(days=6)
    domain = [alt.DateTime(year=day.year, month=day.month, date=day.day) for day in (start, stop)]
    x = alt.X("date:T", scale=alt.Scale(domain=domain), axis=alt.Axis(title=None, format="%d %b"))
    y = alt.Y(
        "used:Q",
        scale=alt.Scale(domain=[0, 110], clamp=True, nice=False),
        axis=alt.Axis(title=None, values=[0, 30, 60, 90], labelExpr="datum.value + '%'"),
    )
    # The plot the page is about takes the darkest ink, whatever its place in the fleet.
    others = iter(PLOT_COLOURS[1:])
    palette = [theme.FOREST if name == focus else next(others) for name in order]
    colour = alt.Color(
        "plot:N", scale=alt.Scale(domain=order, range=palette), legend=alt.Legend(title=None)
    )
    tooltip = [
        alt.Tooltip("plot:N", title="plot"),
        alt.Tooltip("date:T", format="%d %b"),
        alt.Tooltip("used:Q", title="reserve used (%)", format=".0f"),
    ]

    def lines(frame: pd.DataFrame, **mark: object) -> list[alt.Chart]:
        """The other plots first and thin, so the focus is drawn over them."""
        base = alt.Chart(frame).encode(x=x, y=y, color=colour, tooltip=tooltip)
        return [
            base.transform_filter(alt.datum.plot != focus).mark_line(strokeWidth=1.3, **mark),
            base.transform_filter(alt.datum.plot == focus).mark_line(strokeWidth=2.6, **mark),
        ]

    history = lines(observed, interpolate="monotone")
    ahead = lines(projected, strokeDash=[2, 3])
    level = pd.DataFrame({"used": [trigger], "label": ["irrigation trigger"]})
    rule = (
        alt.Chart(level)
        .mark_rule(color=theme.MUTED, strokeDash=[5, 4], strokeWidth=1)
        .encode(y="used:Q")
    )
    rule_label = (
        alt.Chart(level)
        .mark_text(align="left", baseline="bottom", dx=3, dy=-3, fontSize=9, color=theme.MUTED)
        .encode(x=alt.value(0), y="used:Q", text="label:N")
    )
    end = projected[projected["plot"] == focus].tail(1).assign(label=callout)
    pill = (
        alt.Chart(end)
        .mark_tick(
            orient="horizontal",
            color=theme.LIME,
            opacity=1,
            thickness=20,
            size=7 * len(callout) + 18,
            cornerRadius=3,
            yOffset=-17,
        )
        .encode(x="date:T", y="used:Q")
    )
    words = (
        alt.Chart(end)
        .mark_text(fontSize=10, fontWeight=600, color=theme.FOREST, dy=-17)
        .encode(x="date:T", y="used:Q", text="label:N")
    )
    return style(alt.layer(rule, rule_label, *history, *ahead, pill, words), height)


def soil_water_chart(
    daily: pd.DataFrame,
    theta_fc: float,
    theta_trigger: float,
    theta_wp: float,
    events: pd.DataFrame | None = None,
    height: int = 236,
) -> alt.LayerChart:
    """The canonical irrigation chart: reference bands and the trace.

    An agronomist reads the answer off it without needing the axis - a trace
    riding the top band is being over-watered and draining money past the roots,
    one grinding along the bottom is losing yield, and the sawtooth between them
    is a well-run plot.
    """
    zones = pd.DataFrame(
        [
            {"lower": theta_trigger, "upper": theta_fc, "zone": "Comfortable"},
            {"lower": theta_wp, "upper": theta_trigger, "zone": "Below refill point"},
        ]
    )
    bands = (
        alt.Chart(zones)
        .mark_rect(opacity=0.8)
        .encode(
            y=alt.Y("lower:Q", title="water content (m³/m³)"),
            y2="upper:Q",
            color=alt.Color(
                "zone:N",
                scale=alt.Scale(
                    domain=["Comfortable", "Below refill point"], range=[theme.CREAM, "#f3dfd8"]
                ),
                legend=alt.Legend(title=None),
            ),
        )
    )
    lines = pd.DataFrame(
        [
            {"level": theta_fc, "label": "field capacity"},
            {"level": theta_trigger, "label": "refill point"},
            {"level": theta_wp, "label": "wilting point"},
        ]
    )
    rules = (
        alt.Chart(lines)
        .mark_rule(strokeDash=[4, 4], color=theme.MUTED, strokeWidth=1)
        .encode(y="level:Q", tooltip=["label", alt.Tooltip("level:Q", format=".3f")])
    )
    trace = (
        alt.Chart(daily)
        .mark_line(color=theme.FOREST, strokeWidth=1.4)
        .encode(
            x=alt.X("date:T", axis=alt.Axis(title=None, format="%b")),
            y=alt.Y("theta_smooth:Q", scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("theta_smooth:Q", format=".3f")],
        )
    )

    layers = [bands, rules, trace]
    if events is not None and not events.empty:
        irrigation = events[events["kind"] == "irrigation"]
        if not irrigation.empty:
            # A rug rather than full-height rules: a plot irrigated twice a week
            # produces a hundred of them and they merge into a wall that hides
            # the trace they are meant to annotate.
            layers.append(
                alt.Chart(irrigation)
                .mark_tick(color=theme.LEAF, thickness=1.2, size=9, opacity=0.9, yOffset=-4)
                .encode(
                    x="date:T",
                    y=alt.datum(theta_wp),
                    tooltip=["kind", alt.Tooltip("magnitude:Q", format=".1f")],
                )
            )
    return style(alt.layer(*layers).resolve_scale(y="shared"), height)


def hinge_chart(points: pd.DataFrame, breakpoint: float, height: int = 208) -> alt.LayerChart:
    """The drainage hinge GAIA fits to recover field capacity."""
    scatter = (
        alt.Chart(points)
        .mark_circle(size=18, opacity=0.5)
        .encode(
            x=alt.X(
                "theta:Q",
                scale=alt.Scale(zero=False),
                axis=alt.Axis(title="water content (m³/m³)"),
            ),
            y=alt.Y("residual_mm:Q", axis=alt.Axis(title="unexplained loss (mm/day)")),
            color=alt.Color(
                "in_fit:N",
                scale=alt.Scale(domain=[True, False], range=[theme.FOREST, theme.SLATE]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("theta:Q", format=".3f"),
                alt.Tooltip("residual_mm:Q", format=".2f"),
            ],
        )
    )
    marker = (
        alt.Chart(pd.DataFrame({"theta": [breakpoint]}))
        .mark_rule(color=theme.LEAF, strokeWidth=2.5, strokeDash=[5, 3])
        .encode(x="theta:Q")
    )
    return style(alt.layer(scatter, marker), height)


def multi_line(
    frame: pd.DataFrame,
    x: str,
    y: str,
    series: str,
    *,
    domain: list[str] | None = None,
    range_: list[str] | None = None,
    y_title: str | None = None,
    height: int = 236,
) -> alt.Chart:
    scale = alt.Scale(domain=domain, range=range_) if domain else alt.Scale(range=PLOT_COLOURS)
    chart = (
        alt.Chart(frame)
        .mark_line(strokeWidth=1.6)
        .encode(
            x=alt.X(x, axis=alt.Axis(title=None, format="%b")),
            y=alt.Y(y, axis=alt.Axis(title=y_title or y), scale=alt.Scale(zero=False)),
            color=alt.Color(series, scale=scale, legend=alt.Legend(title=None)),
            tooltip=list(frame.columns[:4]),
        )
    )
    return style(chart, height)


def series_chart(frame: pd.DataFrame, x: str, y: str, y_title: str, height: int = 196) -> alt.Chart:
    chart = (
        alt.Chart(frame[[x, y]])
        .mark_line(color=theme.FOREST, strokeWidth=1.3)
        .encode(
            x=alt.X(f"{x}:T", axis=alt.Axis(title=None, format="%d %b")),
            y=alt.Y(f"{y}:Q", scale=alt.Scale(zero=False), axis=alt.Axis(title=y_title)),
            tooltip=[
                alt.Tooltip(f"{x}:T", format="%d %b %H:%M"),
                alt.Tooltip(f"{y}:Q", format=",.3~f"),
            ],
        )
    )
    return style(chart, height)


def policy_bars(
    frame: pd.DataFrame, value: str, title: str, fmt: str, policies: list[str], height: int = 214
) -> alt.Chart:
    chart = (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X(f"{value}:Q", axis=alt.Axis(title=title, format=fmt)),
            y=alt.Y("Plot:N", title=None, sort=None),
            yOffset=alt.YOffset("Policy:N", sort=policies),
            color=alt.Color(
                "Policy:N",
                scale=alt.Scale(domain=policies, range=POLICY_COLOURS),
                legend=alt.Legend(title=None),
            ),
            tooltip=["Plot", "Policy", alt.Tooltip(f"{value}:Q", format=fmt)],
        )
    )
    return style(chart, height)


def airtime_bars(frame: pd.DataFrame, allowance: float, height: int = 150) -> alt.Chart:
    parts = frame.assign(Remaining=(allowance - frame["used"]).clip(lower=0.0))
    parts = parts.rename(columns={"used": "Used"}).melt(
        "Plot", value_vars=["Used", "Remaining"], var_name="part", value_name="seconds"
    )
    chart = (
        alt.Chart(parts)
        .mark_bar(cornerRadius=3)
        .encode(
            x=alt.X(
                "seconds:Q",
                stack="zero",
                scale=alt.Scale(domain=[0, allowance]),
                axis=alt.Axis(title="seconds of airtime a day"),
            ),
            y=alt.Y("Plot:N", title=None, sort=None),
            color=alt.Color(
                "part:N",
                scale=alt.Scale(domain=["Used", "Remaining"], range=[theme.FOREST, "#cddbd3"]),
                legend=alt.Legend(title=None),
            ),
            order=alt.Order("part:N", sort="descending"),
            tooltip=["Plot", "part", alt.Tooltip("seconds:Q", format=".1f")],
        )
    )
    return style(chart, height)
