r"""Recovering the soil's hydraulic constants from its own drainage curves.

A farmer buying this product does not know their field capacity. They will not
send soil to a laboratory, and a textbook value for "clay loam" spans a wider
range than the decision needs: published field capacities for that one texture
class run from 0.29 (Virginia Cooperative Extension) through 0.31 (FAO-56
Table 19) to 0.344 measured in situ (Jabro et al., USDA-ARS). Choosing wrongly
inside that range moves the irrigation trigger by tens of millimetres.

So the constants are recovered from the plot's own behaviour.

Method
------
The classical procedure - wet the soil, watch it drain, call the asymptote field
capacity - assumes gravity is the only thing removing water, which is why the
literature says to run it on bare soil before the season starts. On a cropped,
rain-fed plot it does not survive contact: the profile only saturates during the
wet season, and during the wet season it keeps raining, so the drainage curve is
never clean. Implemented directly it finds two or three candidate windows in a
year and none of them fit.

What works instead uses every day of the record. On any day the profile is not
being wetted, the water that left it went two places - the canopy transpired it,
or gravity drained it:

.. math:: -\Delta S = ET_c + D

GAIA already knows :math:`ET_c`: it computes FAO-56 reference evapotranspiration
from the temperature, humidity and illuminance channels and scales it by the
crop coefficient. So the residual

.. math:: R = -\Delta\theta \, z_r \cdot 1000 - ET_c

*is* the drainage, in millimetres. And drainage only happens above field
capacity. Plotting :math:`R` against :math:`\theta` therefore produces a hinge:
flat and near zero while the profile is below field capacity, rising once it is
above. Fitting

.. math:: R(\theta) = a \max(0, \theta - \theta_{fc})

recovers the breakpoint from several hundred observations rather than three, and
needs no rain gauge - because wetting days are identified by the probe itself
and simply excluded.

The lower limit is a different matter. It is only observable on a plot allowed
to dry to it, and an irrigated plot never is; where the season does not provide
the evidence the texture prior stands, and the estimate says so.

References
----------
References
----------
Jabro, J.D. et al. *In-situ soil-water retention and field water capacity in
two contrasting soil textures* (USDA-ARS). Datta, Taghvaeian & Stivers (2017),
*Understanding Soil Water Content and Thresholds*. FAO-56 Chapter 8.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

#: A jump in water content this large from one day to the next is a wetting
#: event, not noise: the probe's own scatter is an order of magnitude smaller.
WETTING_JUMP = 0.012
#: Drainage segments shorter than this carry too little curvature to fit.
MIN_SEGMENT_DAYS = 4
#: Only the first stretch after wetting is drainage; after this the crop's
#: extraction dominates, the correction for it accumulates error, and the curve
#: is no longer describing gravity.
MAX_DRAINAGE_DAYS = 10
#: A rise larger than this inside a drainage window means water came in - a
#: shower too small to register as a wetting event, or a top-up irrigation.
#: Either way the window is no longer pure drainage and has to be cut short.
MAX_UPTICK = 0.004
#: Observations above the breakpoint needed before the hinge is trusted. A
#: sandy soil drains so fast that the profile is almost never caught above
#: field capacity, and a plot irrigated on a fixed calendar is re-wetted before
#: it ever settles - in both cases the rising arm of the hinge has nothing in
#: it, and the fit slides upward into the wet tail. Reporting the texture prior
#: and saying so beats reporting a confident wrong number.
MIN_DRAINING_OBSERVATIONS = 20
#: A changepoint needs data on *both* sides of it. Guarding only the upper end
#: is a half-criterion: a breakpoint that lands below almost every observation
#: has an empty lower arm, the hinge degenerates into a plain straight line, and
#: the fit is accepted with a confident number near the wilting point.
MIN_POINTS_BELOW = 20
#: Only days in the wetter half of the record are eligible for the fit.
#:
#: Below that, the crop is often under water stress, and a stressed crop
#: transpires ``Ks * ETc`` while the residual subtracts the full ``ETc``. The
#: shortfall grows as the soil dries, which paints a *rising* relationship
#: between water content and residual at the dry end - a hinge that looks
#: exactly like drainage and is nothing of the sort. Restricting the domain
#: removes the confound without needing to know the stress coefficient, which
#: cannot be computed before field capacity is known.
WET_HALF_QUANTILE = 0.50
#: A breakpoint above this quantile of the observed water content is rejected.
#: The hinge is only identified if there is data on *both* sides of it; when the
#: fit slides into the wet tail it is describing the last few points rather than
#: a change in regime, and no count threshold distinguishes that reliably -
#: raising the count until the bad cases disappear is tuning to the answer,
#: whereas asking whether the breakpoint sits inside the data is a question
#: about identifiability.
MAX_BREAKPOINT_QUANTILE = 0.85
#: Only wetting events that push the profile into the top of its observed range
#: are worth analysing.
#:
#: This is the constraint that makes the whole recovery subtle: a *well
#: managed* plot never saturates. The refill buffer that stops irrigation water
#: draining away also stops the profile from ever climbing past field capacity,
#: so the plot produces no drainage curve to learn from. The calibration has to
#: come from the wet season, when rain overfills the profile whatever the
#: irrigation controller wanted.
SATURATION_QUANTILE = 0.90


@dataclass(slots=True)
class DrainageSegment:
    """One post-wetting drainage curve and what it implies."""

    start_index: int
    days: int
    theta_start: float
    #: Asymptote of the fitted decay - this segment's field-capacity estimate.
    theta_asymptote: float
    #: Drainage time constant in days; texture's fingerprint.
    tau_days: float
    r_squared: float

    #: How much drainage the curve actually captured, in water content.
    drainage_amplitude: float = 0.0

    @property
    def usable(self) -> bool:
        """Accept only fits with real curvature behind them.

        R-squared alone is the wrong gate: a window where drainage finished on
        day one is almost flat, so its variance is noise and even a perfect fit
        scores badly. Requiring a minimum drained amount first is what
        separates "no drainage happened" from "the fit failed".
        """
        return (
            self.drainage_amplitude > 0.004
            and self.r_squared > 0.60
            and 0.05 < self.theta_asymptote < 0.65
            and 0.3 < self.tau_days < 60.0
        )


@dataclass(slots=True)
class SoilStateEstimate:
    """What GAIA concluded about the soil, from the soil."""

    plot_id: str
    theta_fc: float
    theta_wp: float
    #: How confident the field-capacity figure is, from the spread across segments.
    theta_fc_std: float
    #: Median drainage time constant, days.
    tau_days: float
    n_segments: int
    #: ``True`` when the lower limit is a texture prior rather than an
    #: observation, which happens on a plot that is never allowed to dry out.
    wilting_point_is_prior: bool
    #: ``True`` when the season never caught the profile draining often enough
    #: to locate the breakpoint, so field capacity is a texture prior too.
    field_capacity_is_prior: bool = False
    #: Days observed above the fitted field capacity - the evidence behind it.
    n_draining_days: int = 0
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    segments: list[DrainageSegment] = field(default_factory=list)

    @property
    def available_water_capacity(self) -> float:
        return max(self.theta_fc - self.theta_wp, 1e-6)

    def taw_mm(self, root_depth_m: float) -> float:
        return self.available_water_capacity * root_depth_m * 1000.0


# --------------------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------------------


def daily_soil_moisture(uplinks: pd.DataFrame) -> pd.DataFrame:
    """Collapse sparse uplinks into a daily trace.

    Pre-dawn readings are the ones to trust: the canopy is not transpiring, the
    profile has had all night to equilibrate, and the probe is not sitting in
    the temperature swing that biases a capacitive measurement. Falling back to
    the daily median keeps a day that produced no early uplink from vanishing -
    which on the radio-limited plot is most of them.
    """
    frame = uplinks.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["date"] = frame["timestamp"].dt.floor("D")
    frame["hour"] = frame["timestamp"].dt.hour

    predawn = frame[frame["hour"].between(3, 7)]
    daily = (
        predawn.groupby("date")
        .agg(theta=("soil_moisture", "median"), n_predawn=("soil_moisture", "size"))
        .reindex(pd.date_range(frame["date"].min(), frame["date"].max(), freq="D"))
    )
    fallback = frame.groupby("date")["soil_moisture"].median()
    daily["theta"] = daily["theta"].fillna(fallback)
    daily["n_predawn"] = daily["n_predawn"].fillna(0).astype(int)
    daily.index.name = "date"
    return daily.reset_index()


def detect_wetting_events(theta: pd.Series, jump: float = WETTING_JUMP) -> list[int]:
    """Indices where the profile was wetted - rain or irrigation."""
    difference = theta.diff()
    raw = list(theta.index[(difference > jump).fillna(False)])
    # Consecutive days of a single storm are one event.
    return [day for position, day in enumerate(raw) if position == 0 or day - raw[position - 1] > 1]


def _decay(t: np.ndarray, asymptote: float, amplitude: float, tau: float) -> np.ndarray:
    return asymptote + amplitude * np.exp(-t / np.maximum(tau, 1e-6))


def fit_drainage_segment(
    theta: np.ndarray, start_index: int, extraction: np.ndarray | None = None
) -> DrainageSegment | None:
    """Fit ``theta(t) = theta_fc + A exp(-t/tau)`` to one drainage curve.

    ``extraction`` is the crop's daily water uptake expressed in the same units
    as ``theta``. Its cumulative sum is added back so the curve describes
    gravity alone.
    """
    if len(theta) < MIN_SEGMENT_DAYS:
        return None

    if extraction is not None:
        theta = theta + np.cumsum(np.concatenate([[0.0], extraction[:-1]]))

    t = np.arange(len(theta), dtype=float)
    span = float(np.ptp(theta))
    guess = (float(theta.min()), max(span, 1e-3), 3.0)
    bounds = ([0.02, 0.0, 0.3], [0.65, 0.4, 120.0])

    try:
        popt, _ = curve_fit(_decay, t, theta, p0=guess, bounds=bounds, maxfev=8000)
    except (RuntimeError, ValueError):
        return None

    residual = theta - _decay(t, *popt)
    total = float(np.sum((theta - theta.mean()) ** 2))
    r_squared = 1.0 - float(np.sum(residual**2)) / total if total > 0 else 0.0

    return DrainageSegment(
        start_index=start_index,
        days=len(theta),
        theta_start=float(theta[0]),
        theta_asymptote=float(popt[0]),
        tau_days=float(popt[2]),
        r_squared=r_squared,
        drainage_amplitude=float(theta[0] - popt[0]),
    )


def _drainage_window(theta: np.ndarray, start: int, events: list[int]) -> int:
    """Last index of a clean drainage run starting at ``start``."""
    end = min(start + MAX_DRAINAGE_DAYS, len(theta))
    following = [event for event in events if start < event < end]
    if following:
        end = following[0]
    # Cut at the first sign of water coming back in.
    for index in range(start + 1, end):
        if theta[index] - theta[index - 1] > MAX_UPTICK:
            return index
    return end


def estimate_field_capacity(
    theta: pd.Series, wetting_events: list[int], extraction: np.ndarray | None = None
) -> tuple[float, float, float, list[DrainageSegment]]:
    """Field capacity from the asymptotes of every clean drainage curve.

    Returns ``(theta_fc, std, tau_days, segments)``. Segments are weighted by
    how much drainage they actually captured: a curve that starts barely above
    its asymptote constrains it far less than one that starts saturated.
    """
    values = theta.to_numpy(dtype=float)
    if len(values) < MIN_SEGMENT_DAYS:
        return (
            float(np.nanmax(values)) if len(values) else float("nan"),
            float("nan"),
            float("nan"),
            [],
        )

    saturated = float(np.nanquantile(values, SATURATION_QUANTILE))
    segments: list[DrainageSegment] = []

    for event in wetting_events:
        if values[event] < saturated:
            continue  # the profile was never full enough to be draining
        end = _drainage_window(values, event, wetting_events)
        if end - event < MIN_SEGMENT_DAYS:
            continue
        window = None if extraction is None else extraction[event:end]
        segment = fit_drainage_segment(values[event:end], event, window)
        if segment:
            segments.append(segment)

    usable = [s for s in segments if s.usable]
    if not usable:
        # Nothing clean to learn from. The best remaining statement about field
        # capacity is the wettest the profile was seen to hold.
        return float(np.nanquantile(values, 0.97)), float("nan"), float("nan"), segments

    asymptotes = np.array([s.theta_asymptote for s in usable])
    weights = np.array([max(s.theta_start - s.theta_asymptote, 1e-4) for s in usable])
    theta_fc = float(np.average(asymptotes, weights=weights))
    spread = float(np.std(asymptotes))
    tau = float(np.median([s.tau_days for s in usable]))
    return theta_fc, spread, tau, segments


def drainage_residuals(
    theta: np.ndarray,
    etc_mm: np.ndarray,
    root_depth_m: np.ndarray,
    wetting: np.ndarray,
    irrigation_mm: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    r"""Unexplained water loss per day, paired with the water content it left.

    From the storage balance on a day with no rain,

    .. math:: \Delta S = I - ET_c - D  \;\Longrightarrow\; D = I - ET_c - \Delta S

    so the residual is drainage plus whatever the evapotranspiration estimate
    got wrong. Returns ``(theta_at_start_of_day, residual_mm)`` over days the
    profile was not being wetted.

    Days the platform irrigated are **excluded**, not corrected. Adding the
    commanded depth back into the balance is the obvious move and it makes
    things worse: on a fast-draining soil the water goes in, pushes the profile
    briefly past field capacity and has largely drained again by the next
    pre-dawn reading, so the water content the residual gets paired with is not
    the water content the drainage happened at. Two thirds of the irrigation
    events here never register as wetting at all for exactly that reason. The
    log's real value is knowing which days to distrust.
    """
    delta_storage = np.diff(theta) * root_depth_m[:-1] * 1000.0
    residual = -delta_storage - etc_mm[:-1]

    contaminated = wetting.copy()
    if irrigation_mm is not None:
        irrigated = np.asarray(irrigation_mm, dtype=float) > 0.5
        contaminated |= irrigated
        contaminated[1:] |= irrigated[:-1]  # the day after is still redistributing

    keep = ~contaminated[:-1] & ~contaminated[1:] & np.isfinite(residual)
    return theta[:-1][keep], residual[keep]


def fit_drainage_hinge(
    theta: np.ndarray, residual: np.ndarray, min_points_above: int = 20
) -> tuple[float, float, int] | None:
    """Least-squares hinge ``R = c + a * max(0, theta - theta_fc)``.

    Returns ``(theta_fc, slope, points_above)``, or ``None`` when no breakpoint
    can be located - which the caller must treat as "use the prior and say so".

    Two details earn their place.

    **The intercept is not optional.** Without it the model asserts that the
    residual is zero below field capacity, and it is not: undetected inputs -
    an irrigation top-up too small to register as a wetting event, a light
    shower - bias the whole cloud downward by a couple of millimetres a day. A
    hinge forced through the origin then finds no elbow at all. What identifies
    field capacity is the *change in slope*, not the absolute level.

    **Failure is returned, not disguised.** An earlier version of this function
    fell back to the texture prior internally and reported it like a fit. The
    caller believed it, the console displayed "measured", and because the prior
    happened to equal the truth in testing the error read 0.0000 - a wrong
    answer that looked like a perfect one.
    """
    if len(theta) < 30:
        return None

    wet_enough = theta >= float(np.quantile(theta, WET_HALF_QUANTILE))
    theta, residual = theta[wet_enough], residual[wet_enough]
    if len(theta) < 30:
        return None

    candidates = np.quantile(theta, np.linspace(0.10, 0.85, 80))
    best: tuple[float, float, float] | None = None

    for breakpoint in candidates:
        above = np.maximum(theta - breakpoint, 0.0)
        if int(np.count_nonzero(above)) < min_points_above:
            continue
        if int(np.count_nonzero(theta < breakpoint)) < MIN_POINTS_BELOW:
            continue

        # Least squares for R = c + a * above, in closed form.
        design = np.column_stack([np.ones_like(above), above])
        try:
            coefficients, *_ = np.linalg.lstsq(design, residual, rcond=None)
        except np.linalg.LinAlgError:
            continue
        intercept, slope = float(coefficients[0]), float(coefficients[1])
        if slope <= 0:
            continue  # drainage cannot decrease as the soil gets wetter

        error = residual - (intercept + slope * above)
        scale = max(1.4826 * float(np.median(np.abs(error - np.median(error)))), 1e-6)
        loss = float(np.mean(np.clip(error, -2.0 * scale, 2.0 * scale) ** 2))
        if best is None or loss < best[2]:
            best = (float(breakpoint), slope, loss)

    if best is None:
        return None
    return best[0], best[1], int(np.count_nonzero(theta > best[0]))


def estimate_wilting_point(
    theta: pd.Series, theta_fc: float, texture_prior_wp: float
) -> tuple[float, bool]:
    """Lower limit of plant-available water.

    Only observable on a plot that is actually allowed to dry down to it - and a
    well-irrigated plot, by design, never is. So the driest reading the season
    produced is treated as an *upper bound* on the wilting point, and where that
    bound is not binding the texture prior stands. Saying which of the two
    answered is the honest part: an estimate that silently falls back to a
    lookup table is a lookup table.
    """
    driest = float(np.nanquantile(theta.to_numpy(dtype=float), 0.02))
    # A plot that never dropped far below field capacity tells us nothing.
    observed_range = theta_fc - driest
    if observed_range < 0.6 * (theta_fc - texture_prior_wp):
        return texture_prior_wp, True
    return min(driest, texture_prior_wp), False


def estimate_soil_state(
    uplinks: pd.DataFrame,
    plot_id: str,
    texture_prior_fc: float,
    texture_prior_wp: float,
    crop_demand_mm: np.ndarray | None = None,
    root_depth_m: np.ndarray | None = None,
    irrigation_mm: np.ndarray | None = None,
) -> SoilStateEstimate:
    """Run the whole recovery for one plot, from uplinks alone.

    ``crop_demand_mm`` and ``root_depth_m`` come from GAIA's own FAO-56
    calculation over the weather channels - not from any hidden state.
    """
    daily = daily_soil_moisture(uplinks)
    if daily.empty:
        return SoilStateEstimate(
            plot_id, texture_prior_fc, texture_prior_wp, float("nan"), float("nan"), 0, True
        )

    # A short median smooths probe scatter without flattening a drainage curve.
    theta = daily["theta"].rolling(3, center=True, min_periods=1).median()
    daily["theta_smooth"] = theta

    extraction = None
    if crop_demand_mm is not None and root_depth_m is not None:
        length = min(len(daily), len(crop_demand_mm), len(root_depth_m))
        extraction = np.zeros(len(daily))
        extraction[:length] = np.asarray(crop_demand_mm[:length], dtype=float) / (
            np.maximum(np.asarray(root_depth_m[:length], dtype=float), 1e-6) * 1000.0
        )

    events = detect_wetting_events(theta)

    # Preferred route: the hinge over the whole record.
    theta_fc = texture_prior_fc
    spread, tau = float("nan"), float("nan")
    fc_is_prior, draining = True, 0
    segments: list[DrainageSegment] = []

    if crop_demand_mm is not None and root_depth_m is not None:
        length = min(len(daily), len(crop_demand_mm), len(root_depth_m))
        wetting = np.zeros(len(daily), dtype=bool)
        wetting[[e for e in events if e < len(daily)]] = True
        # The day after a wetting event is still redistributing, not draining.
        wetting[1:] |= wetting[:-1]

        applied = None
        if irrigation_mm is not None:
            applied = np.zeros(length)
            applied[: min(length, len(irrigation_mm))] = np.asarray(
                irrigation_mm[:length], dtype=float
            )

        theta_points, residuals = drainage_residuals(
            theta.to_numpy(dtype=float)[:length],
            np.asarray(crop_demand_mm[:length], dtype=float),
            np.asarray(root_depth_m[:length], dtype=float),
            wetting[:length],
            applied,
        )
        fit = fit_drainage_hinge(theta_points, residuals, MIN_DRAINING_OBSERVATIONS)
        if fit is not None:
            candidate, _slope, draining = fit
            # The breakpoint has to sit inside the data. One that lands in the
            # wet tail is describing the last few points, not a change of regime.
            if candidate <= float(np.quantile(theta_points, MAX_BREAKPOINT_QUANTILE)):
                theta_fc, fc_is_prior = candidate, False
        spread = (
            float(np.std(theta_points[theta_points > theta_fc]))
            if np.any(theta_points > theta_fc)
            else float("nan")
        )

    # The segment fits are kept for the console: they are what a reader expects
    # to see, and showing that they find almost nothing is the honest display.
    _fc_segments, _s, tau_segments, segments = estimate_field_capacity(theta, events, extraction)
    tau = tau_segments
    theta_wp, is_prior = estimate_wilting_point(theta, theta_fc, texture_prior_wp)

    daily["is_wetting_event"] = daily.index.isin(events)
    return SoilStateEstimate(
        plot_id=plot_id,
        theta_fc=theta_fc,
        theta_wp=theta_wp,
        theta_fc_std=spread,
        tau_days=tau,
        n_segments=len([s for s in segments if s.usable]),
        wilting_point_is_prior=is_prior,
        field_capacity_is_prior=fc_is_prior,
        n_draining_days=draining,
        daily=daily,
        segments=segments,
    )
