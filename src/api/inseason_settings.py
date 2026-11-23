"""Settings gear: GET/PUT /api/inseason/settings (src/inseason/settings.py)."""
from __future__ import annotations

import yaml
from fastapi import HTTPException
from pydantic import BaseModel

from src.api import inseason as ins
from src.inseason import settings as st
from src.inseason.model import INSEASON_CONFIG_PATH


def _base() -> dict:
    with open(INSEASON_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


@ins.router.get("/settings")
def get_settings() -> dict:
    return {"groups": st.describe(_base(), st.read_overrides()),
            "file": str(st.SETTINGS_PATH)}


class SettingsIn(BaseModel):
    values: dict = {}
    reset_all: bool = False


@ins.router.put("/settings")
def put_settings(body: SettingsIn) -> dict:
    base = _base()
    over = {} if body.reset_all else st.read_overrides()
    try:
        new = st.update(base, over, body.values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    st.write_overrides(new)
    ins._CACHE.clear()
    ins._FA_CACHE.clear()
    return {"groups": st.describe(base, new), "file": str(st.SETTINGS_PATH)}


class InjuryReturnIn(BaseModel):
    player_id: int
    date: str | None = None      # None / "" = back to the estimate from his tag


@ins.router.put("/injury-return")
def put_injury_return(body: InjuryReturnIn) -> dict:
    """Your own return date for an injured player (player card)."""
    from src.inseason.teams import set_injury_return
    try:
        cur = set_injury_return(body.player_id, body.date or None)
    except ValueError:
        raise HTTPException(400, "date: use YYYY-MM-DD")
    ins._CACHE.clear()
    ins._FA_CACHE.clear()
    return {"player_id": body.player_id, "date": cur.get(body.player_id)}
