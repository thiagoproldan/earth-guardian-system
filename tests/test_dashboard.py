"""The console, driven the way a visitor would drive it.

Nothing else in the suite imports the dashboard, which is how a restyle once
left it reaching for a palette that no longer existed while every test stayed
green. These run the real script against a real store: every menu item, for
every plot.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from earthguardian.config import PLOTS, get_settings
from earthguardian.dashboard import theme
from earthguardian.pipeline import generate_and_ingest

APP = str(Path(__file__).resolve().parents[1] / "earthguardian" / "dashboard" / "app.py")
#: Long enough for every stage to have something to say, as in the CI pipeline job.
DAYS = 120
#: The first run analyses the whole store and the Reports page replays the season.
TIMEOUT = 300


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """A season in a store of its own, so the tests never touch ``data/``."""
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("EARTHGUARDIAN_HOME", str(home))
        get_settings.cache_clear()
        generate_and_ingest(date(2025, 1, 1), DAYS)
        yield home
    get_settings.cache_clear()


def console() -> AppTest:
    """A fresh session, with nothing cached from a store another test built."""
    get_settings.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    return AppTest.from_file(APP, default_timeout=TIMEOUT)


def failures(app: AppTest) -> list[str]:
    return [f"{error.message}\n{''.join(error.stack_trace)}" for error in app.exception]


def test_an_empty_store_says_how_to_fill_it(tmp_path, monkeypatch):
    monkeypatch.setenv("EARTHGUARDIAN_HOME", str(tmp_path))
    app = console().run()
    get_settings.cache_clear()

    assert not failures(app)
    assert "No curated data yet" in app.warning[0].value
    assert "earthguardian simulate" in app.code[0].value


def test_the_menu_is_the_one_the_thesis_drew(store):
    app = console().run()

    assert not failures(app)
    assert list(app.radio(key="page").options) == theme.NAV


@pytest.mark.parametrize("plot_id", [plot.plot_id for plot in PLOTS])
def test_every_menu_item_renders_for_every_plot(store, plot_id):
    app = console().run()
    app.selectbox(key="field").set_value(plot_id).run()

    for item in theme.NAV:
        app.radio(key="page").set_value(item).run()
        assert not failures(app), f"{item} failed for {plot_id}:\n" + "\n".join(failures(app))
        markup = " ".join(block.value for block in app.markdown)
        # The layout of the figure: the row of cards, and the strip under the panels.
        assert "eg-cards" in markup, item
        assert "eg-strip" in markup, item
