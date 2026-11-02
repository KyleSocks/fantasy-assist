#!/usr/bin/env bash
# Pull the latest code and rebuild/restart the container. Data and config
# (./data, ./config) are bind mounts, so they survive.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
