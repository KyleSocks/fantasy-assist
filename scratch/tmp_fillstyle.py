"""Find how uPlot colors axis tick text (fillStyle usage near axes drawing)."""
import re

t = open("static/vendor/uplot.min.js", encoding="utf-8").read()
for m in re.finditer(r"fillStyle", t):
    i = m.start()
    print("----")
    print(t[max(0, i - 220):i + 80].replace("\n", " "))
