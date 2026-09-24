import math
from pathlib import Path
from src.projections.projections import explain, _load_config
from src.projections import projections as P

DB = Path(__file__).resolve().parent.parent / "data" / "hockey.db"
SEASON = 20262027
cfg = _load_config()


def pop_std(vals):
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


# ---- McDavid goals ----
t = explain(8478402, SEASON, str(DB))
raw = t["raw_rates"]["goals"]
reg = t["regressed_rate"]["goals"]
af = t["age_factor"]
pgp = t["proj_gp"]
final = t["final"]["goals"]
actuals = [40, 33, 49]  # goals totals, oldest->newest (2324,2425,2526)
sigma = pop_std(actuals)
shrink = abs(raw - reg) * af * pgp
hw = sigma + shrink
print("MCDAVID goals")
print("  raw", raw, "reg", reg, "af", af, "pgp", pgp, "final", final)
print("  sigma", sigma, "shrink", shrink, "hw", hw)
print("  band", [final - hw, final + hw], "expect [27.790579, 42.722749]")

# also per-GP-rate sigma variant
rates = [40/101, 33/89, 49/88]
print("  sigma_rate", pop_std(rates), "-> scaled", pop_std(rates) * af * pgp)

# ---- Tomkins save_pct ----
tg = explain(8477035, SEASON, str(DB))
raw_sp = tg["raw_rates"]["save_pct"]
reg_sp = tg["regressed_rate"]["save_pct"]
final_sp = tg["final"]["save_pct"]
sigma_sp = pop_std([165/185])  # one season -> 0
hw_sp = sigma_sp + abs(raw_sp - reg_sp)
print("\nTOMKINS save_pct")
print("  raw", raw_sp, "reg", reg_sp, "final", final_sp)
print("  sigma", sigma_sp, "hw", hw_sp)
print("  band", [final_sp - hw_sp, final_sp + hw_sp], "expect [0.891892, 0.905644]")

# ---- Tomkins other categories: check degenerate ----
print("\nTOMKINS other cats (final vs regressed/raw presence)")
for cat in ("wins", "shutouts", "games_started", "goals_against", "saves"):
    print("  ", cat, "final", tg["final"].get(cat), "in raw_rates?", cat in tg["raw_rates"])

print("\nTOMKINS n", tg["n"], "k", tg["k"], "pos_mean", tg["pos_mean"])
print("MCDAVID n", t["n"], "k", t["k"], "pos_mean goals", t["pos_mean"]["goals"])
