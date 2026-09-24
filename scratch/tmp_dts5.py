"""Grep d.ts for the Band interface and bbox() docs."""
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

k = text.find("interface Band")
print(text[k - 200:k + 700] if k >= 0 else "Band NOT FOUND")
print("=======")
j = text.find("bbox()")
print(text[j - 200:j + 400])
