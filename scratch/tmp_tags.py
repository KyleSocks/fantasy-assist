"""List uPlot tags + default branch from GitHub API."""
import json
import urllib.request


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return urllib.request.urlopen(req, timeout=30).read()


tags = json.loads(get("https://api.github.com/repos/leeoniya/uPlot/tags?per_page=10"))
print([t["name"] for t in tags])
info = json.loads(get("https://api.github.com/repos/leeoniya/uPlot"))
print("default_branch:", info["default_branch"])
