"""Final confirmation: build the kyle_12_points board and print the top 24.

Run: uv run python tmp_final_board.py
"""
from src.api.routes import build_board


def main() -> None:
    b = build_board("kyle_12_points")
    print("season", b["season"], "count", b["count"], "has_adp", b["has_adp"])
    print(
        "goalies in top 24:",
        [p["name"] for p in b["players"][:24] if p["pos"] == "G"],
    )
    print()
    print("Top 24 (kyle_12_points):")
    for p in b["players"][:24]:
        print(
            f"{p['rank']:>3}  {p['name'][:20]:20s} {p['pos']:2s} "
            f"{p['team']:4s} vorp={p['vorp']:8.2f}  dollars={p['dollars']:5.1f}"
        )


if __name__ == "__main__":
    main()
