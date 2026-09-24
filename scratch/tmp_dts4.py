"""Grep d.ts for bbox() docs and scale key names."""
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

j = text.find("bbox()")
print(text[max(0, j - 500):j + 300])
print("=======")
for needle in ('scales?:', 'left:', 'bottom:'):
    k = text.find(needle)
    print(needle, "->", text[max(0, k - 300):k + 100].replace("\n", " "))
