"""Presentation helpers.

Chart styling is applied per-chart and always *last*. Altair refuses to layer a
chart that already carries a ``config``, so any helper that might end up inside
a layer has to return an unstyled chart and let the caller style the composition
- a rule that is easy to state and easy to violate accidentally.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

PALETTE = {
    "water": "#4ade80",  # the crop is comfortable
    "trigger": "#fbbf24",  # approaching the refill point
    "stress": "#f87171",  # below it
    "truth": "#94a3b8",  # the hidden state, for scoring only
    "radio": "#60a5fa",
    "muted": "#8b949e",
    "grid": "#21262d",
}

SEVERITY_COLOURS = {
    "critical": "#f87171",
    "high": "#fbbf24",
    "medium": "#60a5fa",
    "info": "#8b949e",
}

CSS = """
<style>
  [data-testid="stAppDeployButton"] { display: none; }
  .block-container { padding-top: 2.2rem; max-width: 1400px; }
  h1, h2, h3 { letter-spacing: -0.01em; }
  [data-testid="stMetricValue"] { font-size: 1.55rem; }
  [data-testid="stMetricLabel"] { color: #8b949e; }
  .eg-card {
      background: #161b22; border: 1px solid #21262d; border-radius: 10px;
      padding: 0.85rem 1.05rem; margin-bottom: 0.65rem;
  }
  .eg-card h4 { margin: 0 0 0.3rem 0; font-size: 0.98rem; }
  .eg-pill {
      display: inline-block; padding: 0.06rem 0.5rem; border-radius: 999px;
      font-size: 0.7rem; font-weight: 600; letter-spacing: 0.03em;
  }
  .eg-note { color: #8b949e; font-size: 0.82rem; line-height: 1.45; }
  .eg-mono { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.78rem; }
</style>
"""


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def style(chart: alt.Chart | alt.LayerChart, height: int = 280) -> alt.Chart:
    """Apply the shared look. Always the last thing done to a chart."""
    return (
        chart.properties(height=height)
        .configure_view(strokeWidth=0)
        .configure_axis(
            grid=True,
            gridColor=PALETTE["grid"],
            domain=False,
            labelColor="#8b949e",
            titleColor="#8b949e",
            labelFontSize=11,
            titleFontSize=11,
            tickColor=PALETTE["grid"],
        )
        .configure_legend(
            labelColor="#c9d1d9", titleColor="#8b949e", orient="top", direction="horizontal"
        )
        .configure_title(color="#e6edf3", fontSize=13, anchor="start")
    )


def card(
    title: str, body: str, pill: str | None = None, pill_colour: str = PALETTE["muted"]
) -> None:
    badge = (
        f'<span class="eg-pill" style="background:{pill_colour}22;color:{pill_colour};">{pill}</span>'
        if pill
        else ""
    )
    st.markdown(
        f'<div class="eg-card"><h4>{title} {badge}</h4><div class="eg-note">{body}</div></div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    st.markdown(f'<div class="eg-note">{text}</div>', unsafe_allow_html=True)


def soil_water_chart(
    daily: pd.DataFrame,
    theta_fc: float,
    theta_trigger: float,
    theta_wp: float,
    events: pd.DataFrame | None = None,
    height: int = 340,
) -> alt.LayerChart:
    """The canonical irrigation chart.

    Three horizontal reference bands - field capacity at the top, the refill
    trigger in the middle, the wilting point at the bottom - with the measured
    water content drawn across them. An agronomist reads the answer off it
    without needing the axis: a trace riding the top band is being over-watered
    and draining money past the roots, one grinding along the bottom is losing
    yield, and the sawtooth in between is a well-run plot.
    """
    zones = pd.DataFrame(
        [
            {"lower": theta_trigger, "upper": theta_fc, "zone": "Comfortable"},
            {"lower": theta_wp, "upper": theta_trigger, "zone": "Below refill point"},
        ]
    )
    bands = (
        alt.Chart(zones)
        .mark_rect(opacity=0.10)
        .encode(
            y=alt.Y("lower:Q", title="volumetric water content"),
            y2="upper:Q",
            color=alt.Color(
                "zone:N",
                scale=alt.Scale(
                    domain=["Comfortable", "Below refill point"],
                    range=[PALETTE["water"], PALETTE["stress"]],
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
        .mark_rule(strokeDash=[4, 4], color=PALETTE["muted"], strokeWidth=1)
        .encode(y="level:Q", tooltip=["label", alt.Tooltip("level:Q", format=".3f")])
    )

    trace = (
        alt.Chart(daily)
        .mark_line(color=PALETTE["water"], strokeWidth=1.6)
        .encode(
            x=alt.X("date:T", axis=alt.Axis(title=None)),
            y=alt.Y("theta_smooth:Q", scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("theta_smooth:Q", format=".3f")],
        )
    )

    layers = [bands, rules, trace]
    if events is not None and not events.empty:
        irrigation = events[events["kind"] == "irrigation"]
        if not irrigation.empty:
            # A rug along the bottom rather than full-height rules. A plot that
            # irrigates twice a week produces a hundred of them in a season, and
            # as rules they merge into a wall that hides the trace they are
            # supposed to annotate.
            layers.append(
                alt.Chart(irrigation)
                .mark_tick(color=PALETTE["radio"], thickness=1, size=9, opacity=0.7, yOffset=-4)
                .encode(
                    x="date:T",
                    y=alt.datum(theta_wp),
                    tooltip=["kind", alt.Tooltip("magnitude:Q", format=".1f")],
                )
            )

    return style(alt.layer(*layers).resolve_scale(y="shared"), height)


def hinge_chart(
    theta: pd.Series, residual: pd.Series, breakpoint: float, height: int = 300
) -> alt.LayerChart:
    """The drainage hinge GAIA fits to recover field capacity.

    Each point is one day the profile was not being wetted: how wet it was
    against how much water left it that the crop cannot account for. Below field
    capacity the cloud sits on zero, above it the points climb - and the elbow
    between them is the parameter.
    """
    points = pd.DataFrame({"theta": theta, "residual_mm": residual})
    scatter = (
        alt.Chart(points)
        .mark_circle(size=18, opacity=0.35, color=PALETTE["radio"])
        .encode(
            x=alt.X("theta:Q", scale=alt.Scale(zero=False), axis=alt.Axis(title="water content")),
            y=alt.Y("residual_mm:Q", axis=alt.Axis(title="unexplained loss (mm/day)")),
            tooltip=[
                alt.Tooltip("theta:Q", format=".3f"),
                alt.Tooltip("residual_mm:Q", format=".2f"),
            ],
        )
    )
    marker = (
        alt.Chart(pd.DataFrame({"x": [breakpoint]}))
        .mark_rule(color=PALETTE["water"], strokeWidth=2, strokeDash=[5, 3])
        .encode(x="x:Q")
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
    title: str = "",
    y_title: str | None = None,
    height: int = 280,
) -> alt.Chart:
    scale = alt.Scale(domain=domain, range=range_) if domain else alt.Scale(scheme="set2")
    chart = (
        alt.Chart(frame)
        .mark_line(strokeWidth=1.7)
        .encode(
            x=alt.X(x, axis=alt.Axis(title=None)),
            y=alt.Y(y, axis=alt.Axis(title=y_title or y), scale=alt.Scale(zero=False)),
            color=alt.Color(series, scale=scale, legend=alt.Legend(title=None)),
            tooltip=list(frame.columns[:4]),
        )
        .properties(title=title)
    )
    return style(chart, height)
