"""Grep the d.ts for the draw hook and valToPos signatures."""
import io
import tarfile
import urllib.request

req = urllib.request.Request(
    "https://registry.npmjs.org/uplot/-/uplot-1.6.32.tgz",
    headers={"User-Agent": "curl/8"},
)
blob = urllib.request.urlopen(req, timeout=60).read()
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
    for member in t.getmembers():
        if member.name.endswith("uPlot.d.ts"):
            text = t.extractfile(member).read().decode("utf-8")
            break

k = text.find("namespace Hooks")
seg = text[k:k + 6000]
i = seg.find("draw")
print(seg[i - 400:i + 500])
print("=======")
for needle in ("valToPos", "posToVal", "posToVal", "hooks?"):
    j = text.find(needle)
    print(needle, "->", text[max(0, j - 200):j + 200].replace("\n", " ") if j >= 0 else "NOT FOUND")
