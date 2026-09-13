"""Command-line interface.

``earthguardian demo`` runs the whole thing; every stage is also available on
its own so a reviewer can poke at one piece without regenerating a season.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from earthguardian import __version__
from earthguardian.config import PLOTS, get_settings

app = typer.Typer(
    name="earthguardian",
    help="Environmental monitoring and irrigation intelligence for smallholder farms.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

DEFAULT_START = date(2025, 1, 1)


def _banner(subtitle: str) -> None:
    console.print(
        Panel.fit(
            f"[bold]Earth Guardian[/bold] [dim]v{__version__}[/dim]\n[green]{subtitle}[/green]",
            border_style="green",
        )
    )


def _table(title: str, columns: list[str]) -> Table:
    table = Table(
        title=title, title_style="bold", header_style="bold green", box=None, pad_edge=False
    )
    for index, column in enumerate(columns):
        table.add_column(column, justify="left" if index == 0 else "right")
    return table


@app.command()
def info() -> None:
    """Show the fleet, the radio budget and what has been generated so far."""
    from earthguardian.cloud.relational import CuratedStore
    from earthguardian.edge.lorawan import PAYLOAD_BYTES, plan_uplinks

    settings = get_settings()
    _banner("configuration")

    fleet = _table("Fleet", ["Plot", "Farm", "Crop / Soil", "ha", "TAW", "RAW"])
    for plot in PLOTS:
        fleet.add_row(
            plot.plot_id,
            f"{plot.municipality}/{plot.state}",
            f"{plot.crop_spec.name} on {plot.soil_spec.name.lower()}",
            f"{plot.area_ha:.1f}",
            f"{plot.taw_mm:.0f} mm",
            f"{plot.raw_mm:.0f} mm",
        )
    console.print(fleet)

    radio = _table("Radio", ["Plot", "Distance", "Link", "Airtime", "Uplinks/day", "Limited by"])
    for plot in PLOTS:
        budget = plan_uplinks(
            plot.gateway_distance_km,
            PAYLOAD_BYTES,
            terrain_loss_db=plot.terrain_loss_db,
            max_uplinks_per_day=settings.samples_per_day,
        )
        radio.add_row(
            plot.plot_id,
            f"{plot.gateway_distance_km:.1f} km",
            f"DR{budget.data_rate.index}/SF{budget.data_rate.spreading_factor} @ {budget.rssi_dbm:.0f} dBm",
            f"{budget.airtime_s * 1000:.0f} ms",
            str(budget.uplinks_per_day),
            "radio" if budget.uplinks_per_day < settings.samples_per_day else "sampling",
        )
    console.print()
    console.print(radio)

    if settings.paths.database.exists():
        store = CuratedStore(settings.paths.database)
        stats = _table(f"Curated store ({store.size_mb:.1f} MB)", ["Table", "Rows"])
        for row in store.table_stats().itertuples():
            stats.add_row(row.table, f"{row.rows:,}")
        console.print()
        console.print(stats)
    else:
        console.print("\n[yellow]No data yet - run [bold]earthguardian simulate[/bold].[/yellow]")


@app.command()
def simulate(
    days: int = typer.Option(365, help="Days of field data to generate."),
    start: str = typer.Option(str(DEFAULT_START), help="First day, YYYY-MM-DD."),
    policy: str = typer.Option("sensor", help="Irrigation policy: sensor, calendar or rainfed."),
) -> None:
    """Simulate the fleet, land uplinks in the raw zone and curate them."""
    from earthguardian.edge.simulator import IrrigationPolicy
    from earthguardian.pipeline import generate_and_ingest

    policies = {
        "sensor": IrrigationPolicy.sensor_driven(0.9),
        "calendar": IrrigationPolicy.calendar(7, 20),
        "rainfed": IrrigationPolicy.rainfed(),
    }
    if policy not in policies:
        console.print(f"[red]Unknown policy {policy!r}. Choose from: {', '.join(policies)}[/red]")
        raise typer.Exit(1)

    _banner(f"simulating {days} days across {len(PLOTS)} plots ({policy} irrigation)")
    report, written = generate_and_ingest(date.fromisoformat(start), days, policy=policies[policy])

    table = _table("Ingestion", ["Stage", "Value"])
    table.add_row("uplinks received", f"{report.uplinks:,}")
    table.add_row("raw objects", f"{report.objects:,}")
    table.add_row("raw zone", f"{report.megabytes:.2f} MB ({report.bytes_per_uplink:.0f} B/uplink)")
    for name, rows in written.items():
        table.add_row(f"curated: {name}", f"{rows:,}")
    console.print(table)
    console.print("\n[green]Done.[/green] Next: [bold]earthguardian analyze[/bold]")


@app.command()
def analyze() -> None:
    """Run GAIA: recover the soil state, score it, raise advisories."""
    from earthguardian.pipeline import analyse

    _banner("GAIA - analysing curated uplinks")
    result = analyse()
    if not result.per_plot:
        console.print("[red]No data. Run [bold]earthguardian simulate[/bold] first.[/red]")
        raise typer.Exit(1)

    soil = _table(
        "Soil state recovered from uplinks",
        ["Plot", "Field capacity", "Source", "Evidence", "vs truth", "Depletion MAE"],
    )
    for plot_id, analysis in result.per_plot.items():
        estimate, score = analysis.soil, analysis.score
        soil.add_row(
            plot_id,
            f"{estimate.theta_fc:.3f}",
            "measured" if not estimate.field_capacity_is_prior else "texture prior",
            f"{estimate.n_draining_days} draining days",
            f"{score.get('theta_fc_error', float('nan')):+.4f}",
            f"{score.get('depletion_mae_mm', float('nan')):.1f} mm",
        )
    console.print(soil)

    console.print()
    for analysis in result.per_plot.values():
        from earthguardian.gaia.advisories import as_briefing

        console.print(
            Panel(
                as_briefing(analysis.advisories, analysis.plot),
                title=analysis.plot.plot_id,
                border_style="blue",
            )
        )


@app.command()
def plan() -> None:
    """Show the irrigation decision for every plot."""
    from earthguardian.pipeline import analyse

    _banner("irrigation planning")
    result = analyse(score_against_truth=False, persist=False)
    if not result.per_plot:
        console.print("[red]No data. Run [bold]earthguardian simulate[/bold] first.[/red]")
        raise typer.Exit(1)

    table = _table(
        "Decisions",
        ["Plot", "Decision", "Depth", "Pumped", "Cost", "Due", "Confidence"],
    )
    for analysis in result.per_plot.values():
        irrigation = analysis.irrigation
        table.add_row(
            irrigation.plot_id,
            irrigation.decision.replace("_", " "),
            f"{irrigation.depth_mm:.0f} mm",
            f"{irrigation.pumped_m3:.0f} m3",
            f"R$ {irrigation.cost_brl:,.0f}",
            str(irrigation.due_on),
            irrigation.confidence,
        )
    console.print(table)
    for analysis in result.per_plot.values():
        console.print(
            f"\n[dim]{analysis.irrigation.plot_id}:[/dim] {analysis.irrigation.rationale}"
        )


@app.command()
def impact(
    days: int = typer.Option(365, help="Horizon of the counterfactual."),
    start: str = typer.Option(str(DEFAULT_START), help="First day, YYYY-MM-DD."),
) -> None:
    """Compare rainfed, calendar and sensor-driven irrigation over the same year."""
    from earthguardian.decisions import run_impact_study

    _banner("impact study - three ways to decide when to irrigate")
    study = run_impact_study(date.fromisoformat(start), days)

    table = _table(
        "Outcomes", ["Plot", "Policy", "Water (m3)", "Yield loss", "Stressed days", "Net (R$)"]
    )
    for outcome in study.outcomes:
        table.add_row(
            outcome.plot_id,
            outcome.policy,
            f"{outcome.pumped_m3:,.0f}",
            f"{outcome.yield_loss:.1%}",
            str(outcome.stressed_days),
            f"{outcome.net_brl:,.0f}",
        )
    console.print(table)

    comparison = study.compare()
    console.print()
    console.print(
        Panel.fit(
            f"Calendar to sensor-driven, across the fleet:\n"
            f"  [bold]{comparison['water_saved_m3'].sum():,.0f} m3[/bold] of water saved "
            f"([bold]{comparison['water_saved_pct'].mean():.0f}%[/bold] on average)\n"
            f"  [bold]R$ {comparison['net_gain_brl'].sum():,.0f}[/bold] better off",
            title="Fleet result",
            border_style="green",
        )
    )
    console.print(
        "[dim]Simulated under the assumptions in earthguardian.config - not a field trial.[/dim]"
    )


@app.command()
def dashboard(port: int = typer.Option(8501, help="Port for the Streamlit server.")) -> None:
    """Launch the operations console."""
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    _banner(f"starting the console on http://localhost:{port}")
    raise typer.Exit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(app_path),
                "--server.port",
                str(port),
                "--server.headless",
                "true",
            ],
            # From the project root, so .streamlit/config.toml - the palette and
            # the typeface - applies wherever the command is typed.
            cwd=get_settings().paths.root,
        )
    )


@app.command()
def demo(days: int = typer.Option(365, help="Days of field data to generate.")) -> None:
    """Run everything: simulate, ingest, curate, analyse, plan, measure."""
    _banner("full pipeline")
    simulate(days=days, start=str(DEFAULT_START), policy="sensor")
    console.rule()
    analyze()
    console.rule()
    plan()
    console.rule()
    impact(days=days, start=str(DEFAULT_START))
    console.print(
        "\n[bold green]Pipeline complete.[/bold green] Launch the console with "
        "[bold]earthguardian dashboard[/bold]."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
