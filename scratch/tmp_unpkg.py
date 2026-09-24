"""Inspect uplot package layout and download the minified bundle."""
import json
import urllib.request


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


meta = json.loads(get("https://registry.npmjs.org/uplot/1.6.32"))
print("tarball:", meta["dist"]["tarball"])
print("unpkg listing:")
print(get("https://unpkg.com/uplot@1.6.32/")[:3000])
