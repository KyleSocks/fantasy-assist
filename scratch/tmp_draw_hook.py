"""Inspect where the 'draw' hook is emitted in the minified uPlot source."""
import re

t = open("static/vendor/uplot.min.js", encoding="utf-8").read()
idx = [m.start() for m in re.finditer(r'"draw"', t)]
print("occurrences:", len(idx))
for i in idx:
    seg = t[max(0, i - 350):i + 120]
    print("----")
    print(seg.replace("\n", " "))
