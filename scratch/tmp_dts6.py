"""Extract uPlot.d.ts band docs + grep minified fillTo + find GitHub repo."""
import io
import re
import tarfile
import urllib.request


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return urllib.request.urlopen(req, timeout=60)


# 1) d.ts band docs
blob = get("https://registry.npmjs.org/uplot/-/uplot-1.6.32.tgz").read()
with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
    for member in t.getmembers():
        if member.name.endswith("uPlot.d.ts"):
            text = t.extractfile(member).read().decode("utf-8")
            break

for m in re.finditer(r"bands", text):
    i = m.start()
    print("=====")
    print(text[max(0, i - 400): i + 300])

# 2) minified fillTo context
t2 = open("static/vendor/uplot.min.js", encoding="utf-8").read()
for m in re.finditer(r"fillTo", t2):
    i = m.start()
    print("----")
    print(t2[max(0, i - 350): i + 250].replace("\n", " "))

# 3) find the GitHub source location
for url in (
    "https://api.github.com/repos/leeoniya/uPlot",
    "https://api.github.com/repos/leeoniya/uplot",
):
    try:
        info = get(url).read().decode("utf-8")
        print("REPO:", info[:400].replace("\n", " "))
    except Exception as e:  # noqa: BLE001
        print("REPO FAIL:", url, e)
