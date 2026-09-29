import re
for f in ("tests/test_recommend.py",):
    lines = open(f, encoding="utf-8").read().splitlines()
    for i,l in enumerate(lines,1):
        s = l.strip()
        if s.startswith("def test_") or s.startswith("class "):
            print(f"{i:4d} {s[:100]}")
