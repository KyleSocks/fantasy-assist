"""Download uplot 1.6.32 from the npm registry tarball and extract uplot.min.js."""
import io
import tarfile
import urllib.request

url = "https://registry.npmjs.org/uplot/-/uplot-1.6.32.tgz"
req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
with urllib.request.urlopen(req, timeout=60) as r:
    blob = r.read()

with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
    for member in t.getmembers():
        if member.name.endswith("uplot.min.js"):
            print("extracting:", member.name)
            with t.extractfile(member) as src:
                data = src.read()
            with open("static/vendor/uplot.min.js", "wb") as out:
                out.write(data)
            print("bytes:", len(data))
            break
    else:
        print("names:", [m.name for m in t.getmembers()])
