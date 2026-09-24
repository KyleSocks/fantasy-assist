import re

t = open("static/vendor/uplot.min.js", encoding="utf-8").read()

# Look for where band objects get a 'series' array built from user fields a/b
for m in re.finditer(r"series", t):
    i = m.start()
    seg = t[max(0, i - 120):i + 120]
    if re.search(r"\b[aA]\b.*\b[bB]\b", seg) and ("band" in seg.lower() or "Band" in seg):
        print("=====")
        print(seg.replace("\n", " "))
        print()

# Also: search for the literal pattern building bands: something like `l.series=[l.a,l.b]`
for m in re.finditer(r"\[.*?a.*?,.*?b.*?\]", t):
    seg = t[m.start(): m.end()]
    if len(seg) < 40:
        print("CAND:", seg)
