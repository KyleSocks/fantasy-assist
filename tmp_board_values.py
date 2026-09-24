"""Emit the current live board values needed to refresh stale test expectations.

Run: uv run python tmp_board_values.py
"""
from __future__ import annotations

from src.api.routes import build_board


def top(board: dict, n: int) -> None:
    for p in board["players"][:n]:
        print(f"  {p['rank']:>3}  {p['name'][:22]:22s} {p['pos']:3s} "
              f"vorp={p['vorp']:8.2f}  dollars={p['dollars']:6.1f}")


def main() -> None:
    kyle = build_board("kyle_12_points")
    print("=== kyle_12_points ===")
    print("count", kyle["count"], "season", kyle["season"],
          "scoring", kyle["scoring_type"], "has_adp", kyle["has_adp"])
    print("categories", kyle["categories"])
    print("roster_slots", kyle["roster_slots"])
    top(kyle, 10)
    goalies_in_top24 = [p for p in kyle["players"][:24] if p["pos"] == "G"]
    print("goalies in top 24:", goalies_in_top24)
    ranks = [p["rank"] for p in kyle["players"]]
    print("ranks sequential 1..count:",
          ranks == list(range(1, kyle["count"] + 1)))
    print()

    roto = build_board("roto_14")
    print("=== roto_14 ===")
    print("count", roto["count"], "scoring", roto["scoring_type"])
    top(roto, 5)
    kyle_order = [p["id"] for p in kyle["players"]]
    roto_order = [p["id"] for p in roto["players"]]
    print("kyle_order != roto_order:", kyle_order != roto_order)
    vorps = [p["vorp"] for p in roto["players"]]
    print("roto rank1 is max vorp:", vorps[0] == max(vorps))
    print()

    points = build_board("points_10")
    print("=== points_10 ===")
    print("count", points["count"], "scoring", points["scoring_type"])
    top(points, 5)
    print()

    standard = build_board("standard_12_cat")
    print("=== standard_12_cat ===")
    print("count", standard["count"], "scoring", standard["scoring_type"])
    top(standard, 5)


if __name__ == "__main__":
    main()
