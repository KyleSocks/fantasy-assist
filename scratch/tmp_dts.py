"""Extract uPlot.d.ts from the tarball and print the Axis options section."""
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

i = text.find("interface Axis")
j = text.find("interface HookData")
print(text[i:i + 3000])
print("...")
# also find hooks interface
k = text.find("interface Hooks")
print(text[k:k + 1200])
