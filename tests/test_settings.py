"""Settings gear (src/inseason/settings.py, src/api/inseason_settings.py)."""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

import src.inseason.settings as st
from src.inseason.model import INSEASON_CONFIG_PATH, load_inseason_config


def base():
    with open(INSEASON_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def test_every_setting_has_a_default():
    """A typo in a key would show an empty field: every one resolves to a
    value in inseason.yaml or the code."""
    b = base()
    for f in st.FIELDS.values():
        assert st.default_of(b, f) is not None, f["key"]


def test_update_keeps_only_changes():
    b = base()
    new = st.update(b, {}, {"drop_margin": "0.2", "skater_k_games.goals": 50,
                            "replacement_fa_rank.F": b["replacement_fa_rank"]["F"]})  # = default
    assert new == {"drop_margin": 0.2, "skater_k_games": {"goals": 50}}
    # back to default: removed, and the emptied parent with it
    assert st.update(b, new, {"skater_k_games.goals": None}) == {"drop_margin": 0.2}
    assert st.update(b, new, {"skater_k_games.goals": b["skater_k_games"]["goals"]}) == {"drop_margin": 0.2}


@pytest.mark.parametrize("key,value,msg", [
    ("drop_margin", "abc", "a number"), ("drop_margin", 1.5, "at most 1"),
    ("trend_days", 7.5, "whole number"), ("skater_play_share", "yes", "on or off"),
    ("market_points", "avg", "one of total, per_game"), ("nope", 1, "not a setting")])
def test_bad_values_are_refused(key, value, msg):
    with pytest.raises(ValueError, match=msg):
        st.update(base(), {}, {key: value})


def test_overrides_are_laid_over_the_config():
    st.write_overrides({"drop_margin": 0.3, "takes": {"gap": 0.2}, "advanced": {"onice_sh_pct_k_shots": {"F": 500}}})
    cfg = load_inseason_config()
    assert cfg["drop_margin"] == 0.3 and cfg["takes"] == {"gap": 0.2}
    assert cfg["advanced"]["onice_sh_pct_k_shots"] == {"F": 500, "D": 2000}   # D kept
    assert cfg["advanced"]["career_sh_pct_k_shots"] == base()["advanced"]["career_sh_pct_k_shots"]
    assert load_inseason_config(INSEASON_CONFIG_PATH)["drop_margin"] == base()["drop_margin"]
    st.write_overrides({})
    assert not st.SETTINGS_PATH.exists()


def test_describe_marks_changes():
    groups = st.describe(base(), {"takes": {"gap": 0.25}})
    items = {i["key"]: i for g in groups for i in g["items"]}
    assert items["takes.gap"] == {**items["takes.gap"], "value": 0.25, "default": 0.10, "changed": True}
    assert items["drop_margin"]["changed"] is False


def test_api_round_trip():
    from src.api.main import app
    c = TestClient(app)
    r = c.put("/api/inseason/settings", json={"values": {"hold_margin": 0.1, "alerts.url": " https://ntfy.sh/x "}})
    assert r.status_code == 200
    items = {i["key"]: i for g in r.json()["groups"] for i in g["items"]}
    assert items["hold_margin"]["value"] == 0.1 and items["alerts.url"]["value"] == "https://ntfy.sh/x"
    assert st.read_overrides() == {"hold_margin": 0.1, "alerts": {"url": "https://ntfy.sh/x"}}
    bad = c.put("/api/inseason/settings", json={"values": {"hold_margin": -1}})
    assert bad.status_code == 400 and "at least 0" in bad.json()["detail"]
    assert st.read_overrides()["hold_margin"] == 0.1                # unchanged
    assert c.put("/api/inseason/settings", json={"reset_all": True}).status_code == 200
    assert st.read_overrides() == {}
    assert c.get("/api/inseason/settings").json()["groups"][0]["items"][0]["changed"] is False
