import re

t = open("static/vendor/uplot.min.js", encoding="utf-8").read()

# Find where band specs are normalized from user opts (look for 'series:' assignment near bands)
for pat in (r"band", r"\.a\b.*\.b\b", r"series:\["):
    for m in re.finditer(pat, t):
        i = m.start()
        seg = t[max(0, i - 250):i + 250]
        if "bands" in seg or "band" in seg.lower():
            print("=====", pat)
            print(seg.replace("\n", " "))
            print()
