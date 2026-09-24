import io
import re
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

# Print the Band / BandSpec / fillTo / clipTo / fmt related type blocks
for key in ("Band", "fillTo", "clipTo", "fmt"):
    for m in re.finditer(key, text):
        i = m.start()
        print(f"===== {key} @ {i}")
        print(text[max(0, i - 150):i + 250].replace("\r", ""))
        print()
