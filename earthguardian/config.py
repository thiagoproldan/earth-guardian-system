"""Every assumption in the system, in one auditable place.

Agronomic constants come from the FAO irrigation and drainage papers, which are
the reference the whole field uses:

* **FAO-56** (Allen et al., 1998) - *Crop evapotranspiration*: the
  Penman-Monteith reference method, crop coefficients (Table 12), rooting
  depths and depletion fractions (Table 22), and soil water holding capacity
  (Table 19).
* **FAO-33** (Doorenbos & Kassam, 1979) - *Yield response to water*: the yield
  response factor ``Ky`` that turns a water deficit into a harvest loss.

Where a value is a mid-range figure rather than a measurement it says so. The
point of collecting them here is that an agronomist can disagree with a number
without reading any code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# --------------------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------------------


def _discover_root() -> Path:
    override = os.environ.get("EARTHGUARDIAN_HOME")
    if override:
        return Path(override).expanduser().resolve()
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd().resolve()


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def raw(self) -> Path:
        """Document-store landing zone - the thesis's NoSQL tier."""
        return self.data / "raw"

    @property
    def curated(self) -> Path:
        """Relational tier holding cleaned, enriched observations."""
        return self.data / "curated"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def database(self) -> Path:
        return self.curated / "earthguardian.db"

    def ensure(self) -> Paths:
        for directory in (self.raw, self.curated, self.outputs):
            directory.mkdir(parents=True, exist_ok=True)
        return self


# --------------------------------------------------------------------------------------
# Soils - FAO-56 Table 19
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Soil:
    """Hydraulic properties of a soil texture class.

    ``theta_fc`` and ``theta_wp`` are volumetric water contents (m3/m3) at field
    capacity and permanent wilting point; the water a crop can actually reach is
    the difference between them, over the depth its roots occupy.
    """

    name: str
    theta_fc: float
    theta_wp: float
    theta_sat: float
    #: Saturated hydraulic conductivity (mm/day) - governs how fast excess water drains.
    ksat_mm_day: float
    #: Exponent of the unsaturated conductivity curve.
    #:
    #: Calibrated against measured drainage times rather than taken from a
    #: table, because it trades off against ``ksat_mm_day`` and only the pair
    #: together determines behaviour. The target is Jabro et al.'s in-situ
    #: observation that drainage becomes negligible about 50 hours after wetting
    #: in a sandy loam and about 450 hours in a clay loam. A single exponent for
    #: every texture cannot hit both: it leaves the sandy loam above field
    #: capacity for a month, thirty times longer than measured.
    drainage_exponent: float
    #: Time constant of gravitational drainage, days. Water above field capacity
    #: does not vanish at midnight: it drains, and how long that takes is the
    #: soil's signature. Jabro et al. measured drainage becoming negligible
    #: about 50 hours after wetting in a sandy loam and about 450 hours in a
    #: clay loam; for an exponential relaxation that is roughly three time
    #: constants, which is where these come from.
    drainage_tau_days: float
    #: Curve-number style runoff fraction for intense rain on this texture.
    runoff_fraction: float

    @property
    def taw_mm_per_m(self) -> float:
        """Total available water, millimetres per metre of root depth."""
        return (self.theta_fc - self.theta_wp) * 1000.0


SOILS: dict[str, Soil] = {
    "sandy_loam": Soil(
        "Sandy loam",
        theta_fc=0.23,
        theta_wp=0.10,
        theta_sat=0.43,
        ksat_mm_day=1060.0,
        runoff_fraction=0.10,
        drainage_exponent=2.4,
        drainage_tau_days=0.6,
    ),
    "clay_loam": Soil(
        "Clay loam",
        theta_fc=0.31,
        theta_wp=0.17,
        theta_sat=0.47,
        ksat_mm_day=62.0,
        runoff_fraction=0.25,
        drainage_exponent=1.6,
        drainage_tau_days=5.0,
    ),
    "clay": Soil(
        "Clay",
        theta_fc=0.36,
        theta_wp=0.22,
        theta_sat=0.51,
        ksat_mm_day=15.0,
        runoff_fraction=0.35,
        drainage_exponent=1.4,
        drainage_tau_days=7.0,
    ),
}


# --------------------------------------------------------------------------------------
# Crops - FAO-56 Tables 12 and 22, FAO-33 for Ky
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Crop:
    """A crop's water demand over its growing season."""

    name: str
    #: Crop coefficients for the initial, mid-season and late-season stages.
    kc_ini: float
    kc_mid: float
    kc_end: float
    #: Stage lengths in days: initial, development, mid-season, late season.
    stage_days: tuple[int, int, int, int]
    #: Maximum effective rooting depth, metres.
    root_depth_m: float
    #: Fraction of total available water a crop can use before it feels stress.
    depletion_fraction: float
    #: FAO-33 yield response factor: relative yield loss per unit relative deficit.
    yield_response_ky: float
    #: Soil pH band the crop wants. Outside it, the limiting factor stops being
    #: water: below roughly 6.0 phosphorus locks up with iron and aluminium, and
    #: below 5.0 aluminium itself turns phytotoxic and attacks the root tips -
    #: at which point irrigating harder achieves nothing.
    ph_min: float = 6.0
    ph_max: float = 7.0
    #: Whether the crop is replanted each season or stays in the ground.
    perennial: bool = False

    @property
    def season_days(self) -> int:
        return sum(self.stage_days)


CROPS: dict[str, Crop] = {
    "lettuce": Crop(
        "Lettuce",
        kc_ini=0.70,
        kc_mid=1.00,
        kc_end=0.95,
        stage_days=(20, 30, 15, 10),
        root_depth_m=0.40,
        depletion_fraction=0.30,
        yield_response_ky=1.05,
        ph_min=6.0,
        ph_max=7.0,
    ),
    "tomato": Crop(
        "Tomato",
        kc_ini=0.60,
        kc_mid=1.15,
        kc_end=0.80,
        stage_days=(30, 40, 40, 25),
        root_depth_m=1.00,
        depletion_fraction=0.40,
        yield_response_ky=1.05,
        ph_min=6.0,
        ph_max=6.8,
    ),
    "coffee": Crop(
        # Perennial with ground cover; the "season" is a calendar year and the
        # coefficient barely moves, which is exactly why its water demand is
        # steady and its stress tolerance matters more than its peak.
        "Coffee",
        kc_ini=0.95,
        kc_mid=1.05,
        kc_end=0.95,
        stage_days=(90, 90, 100, 85),
        root_depth_m=1.20,
        depletion_fraction=0.40,
        yield_response_ky=0.85,
        # Coffee is an acid-tolerant understorey species and wants a lower band
        # than the vegetables; liming it to a vegetable pH would be a mistake.
        ph_min=5.5,
        ph_max=6.5,
        perennial=True,
    ),
}


# --------------------------------------------------------------------------------------
# The monitored plots
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Plot:
    """One instrumented plot on one smallholder farm."""

    plot_id: str
    farm: str
    municipality: str
    state: str
    latitude: float
    longitude: float
    #: Metres above sea level - Penman-Monteith needs it for atmospheric pressure.
    altitude_m: float
    timezone_offset: int
    area_ha: float
    soil: str
    crop: str
    #: Distance from the plot node to the LoRaWAN gateway, kilometres.
    gateway_distance_km: float
    #: Extra path loss from what sits in the way - a ridge, a valley wall, a
    #: windbreak. Two nodes the same distance out can have very different links.
    terrain_loss_db: float
    #: Irrigation system efficiency: drip loses little, sprinkler loses more.
    irrigation_efficiency: float
    #: What the farmer pays for water, BRL per cubic metre.
    water_cost_brl_m3: float
    #: Gross revenue of an unstressed harvest, BRL per hectare.
    expected_revenue_brl_ha: float
    #: Calendar month the crop is sown, or the year's start for a perennial.
    planting_month: int
    #: Mean annual rainfall, millimetres - shapes the synthetic weather.
    annual_rainfall_mm: float
    #: Calendar month at the peak of the wet season.
    wet_season_peak_month: int
    profile: str

    @property
    def soil_spec(self) -> Soil:
        return SOILS[self.soil]

    @property
    def crop_spec(self) -> Crop:
        return CROPS[self.crop]

    @property
    def taw_mm(self) -> float:
        """Total available water in the root zone, millimetres."""
        return self.soil_spec.taw_mm_per_m * self.crop_spec.root_depth_m

    @property
    def raw_mm(self) -> float:
        """Readily available water - the part usable before stress begins."""
        return self.taw_mm * self.crop_spec.depletion_fraction


PLOTS: tuple[Plot, ...] = (
    Plot(
        plot_id="SP-IBIUNA-01",
        farm="Sitio Boa Esperanca",
        municipality="Ibiuna",
        state="SP",
        latitude=-23.66,
        longitude=-47.22,
        altitude_m=920.0,
        timezone_offset=-3,
        area_ha=0.8,
        soil="clay_loam",
        crop="lettuce",
        gateway_distance_km=2.1,
        terrain_loss_db=6.0,  # rolling ground, scattered tree lines
        irrigation_efficiency=0.75,  # sprinkler
        water_cost_brl_m3=0.42,
        expected_revenue_brl_ha=48_000.0,
        planting_month=3,
        annual_rainfall_mm=1400.0,
        wet_season_peak_month=1,
        profile="Family market garden in the vegetable belt around Sao Paulo. "
        "Short lettuce cycles, shallow roots and a heavy soil - it holds water "
        "well but drains slowly, so overwatering is as costly as underwatering.",
    ),
    Plot(
        plot_id="ES-VENDANOVA-02",
        farm="Fazenda Santa Rita",
        municipality="Venda Nova do Imigrante",
        state="ES",
        latitude=-20.34,
        longitude=-41.13,
        altitude_m=780.0,
        timezone_offset=-3,
        area_ha=3.5,
        soil="clay",
        crop="coffee",
        gateway_distance_km=6.4,
        terrain_loss_db=10.0,  # the node sits in a valley, the gateway on the ridge
        irrigation_efficiency=0.88,  # drip
        water_cost_brl_m3=0.35,
        expected_revenue_brl_ha=22_000.0,
        planting_month=1,
        annual_rainfall_mm=1250.0,
        wet_season_peak_month=12,
        profile="Mountain coffee smallholding. Deep roots and a forgiving crop, "
        "but the node sits 6 km from the gateway, so the radio link - not the "
        "agronomy - is what limits how often it can report.",
    ),
    Plot(
        plot_id="CE-GUARACIABA-03",
        farm="Sitio Serra Verde",
        municipality="Guaraciaba do Norte",
        state="CE",
        latitude=-4.17,
        longitude=-40.75,
        altitude_m=910.0,
        timezone_offset=-3,
        area_ha=1.4,
        soil="sandy_loam",
        crop="tomato",
        gateway_distance_km=3.8,
        terrain_loss_db=8.0,  # open plateau, one shelterbelt in the path
        irrigation_efficiency=0.90,  # drip
        water_cost_brl_m3=0.58,
        expected_revenue_brl_ha=95_000.0,
        planting_month=6,
        annual_rainfall_mm=900.0,
        wet_season_peak_month=3,
        profile="Highland tomato on the Ibiapaba ridge. Sandy soil with little "
        "buffer and a long dry season: the plot can go from comfortable to "
        "stressed in three days, which is faster than a weekly irrigation "
        "calendar can react.",
    ),
)

PLOTS_BY_ID: dict[str, Plot] = {plot.plot_id: plot for plot in PLOTS}


def get_plot(plot_id: str) -> Plot:
    try:
        return PLOTS_BY_ID[plot_id]
    except KeyError:
        known = ", ".join(PLOTS_BY_ID)
        raise KeyError(f"unknown plot {plot_id!r}; known plots: {known}") from None


# --------------------------------------------------------------------------------------
# Runtime settings
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    paths: Paths
    #: Master seed; every stochastic component derives its own stream from it.
    seed: int = 20241120  # the day the thesis was submitted
    #: How often the Arduino samples its sensors, in minutes.
    sample_interval_min: int = 15
    default_days: int = 365
    panel: None = None

    @property
    def samples_per_day(self) -> int:
        return 24 * 60 // self.sample_interval_min


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(paths=Paths(root=_discover_root()))
