import json, sqlite3
d = json.load(open("before_board.json"))
print("keys:", list(d.keys()))
pl = d["players"]
print("count:", len(pl))
for r in pl[:5]:
    print(r)
c = sqlite3.connect("data/hockey.db")
print("tbl 20232024 gtype2:", c.execute("select count(*) from schedule where season_id=20232024 and game_type=2 and (home_abbrev='TBL' or away_abbrev='TBL')").fetchone())
print("min 2627:", c.execute("select count(*) from schedule where season_id=20262027 and game_type=2 and (home_abbrev='MIN' or away_abbrev='MIN')").fetchone())
print("cgy 2627:", c.execute("select count(*) from schedule where season_id=20262027 and game_type=2 and (home_abbrev='CGY' or away_abbrev='CGY')").fetchone())
print("nyr 2627:", c.execute("select count(*) from schedule where season_id=20262027 and game_type=2 and (home_abbrev='NYR' or away_abbrev='NYR')").fetchone())
print("edmonton 2627:", c.execute("select count(*) from schedule where season_id=20262027 and game_type=2 and (home_abbrev='EDM' or away_abbrev='EDM')").fetchone())