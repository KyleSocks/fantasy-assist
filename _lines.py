import sys
path = sys.argv[1]
start, end = int(sys.argv[2]), int(sys.argv[3])
for i, line in enumerate(open(path, encoding="utf-8"), 1):
    if start <= i <= end:
        print(f"{i:5d} {line.rstrip(chr(10))}")