"""Grep minified uPlot for band drawing logic."""
import re

t = open("static/vendor/uplot.min.js", encoding="utf-8").read()
for m in re.finditer(r"bands", t):
    i = m.start()
    seg = t[max(0, i - 300):i + 200]
    print("----")
    print(seg.replace("\n", " "))
