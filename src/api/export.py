"""CSV / JSON export of the draft board (``GET /api/export``).

The board payload (built by ``build_board`` in ``routes.py``) is exported
as a flat CSV (one row per player, category stats as columns) or as the
raw JSON payload. No projection work happens here; this only reshapes the
already-computed board.
"""
from __future__ import annotations

import csv
import io


def board_to_csv(board: dict) -> str:
    """Flatten the board payload into CSV text (header + one row per player).

    Columns are the fixed identity/rank fields followed by one column per
    category key, in the board's ``categories`` order. ``stats`` values are
    written as-is (already rounded to 1 dp by ``build_board``); missing
    stats become empty cells so the column count stays stable.
    """
    cat_keys = board["categories"]
    header = ["id", "name", "pos", "team", "rank", "vorp", "dollars"] + list(cat_keys)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for p in board["players"]:
        row = [
            p["id"], p["name"], p["pos"], p["team"], p["rank"],
            p["vorp"], p["dollars"],
        ]
        row += [p["stats"].get(k, "") for k in cat_keys]
        writer.writerow(row)
    return buf.getvalue()
