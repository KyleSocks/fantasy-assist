"""Print the Hooks defs (draw hook) and Seri interface from uPlot.d.ts."""
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

k = text.find("interface Hooks")
print(text[k:k + 2600])
print("==========")
s = text.find("interface Seri")
print(text[s:s + 1400])
