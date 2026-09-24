"""Grep uPlot 1.6.32 for 'split' (custom ticks) and axis 'fmt'."""
import urllib.request


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return urllib.request.urlopen(req, timeout=30).read()


base = "https://api.github.com/repos/leeoniya/uPlot"
for name in ("uPlot.js", "opts.js"):
    url = "https://raw.githubusercontent.com/leeoniya/uPlot/1.6.32/src/" + name
    text = get(url).decode("utf-8")
    low = text.lower()
    for needle in ("split", "fmt"):
        print("#####", name, needle)
        i = low.find(needle)
        n = 0
        while i != -1 and n < 8:
            print("----- at", i)
            print(text[max(0, i - 250): i + 350])
            i = low.find(needle, i + 1)
            n += 1
