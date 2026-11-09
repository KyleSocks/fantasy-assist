#!/usr/bin/env bash
# Update fantasy-assist on the server: back up the DB, pull, rebuild, wait
# for the health check, and roll back to the previous image if the new one
# doesn't come up healthy. Data and config (./data, ./config) are bind
# mounts, so they survive either way.
#
#   ./deploy/update.sh            normal update
#   ./deploy/update.sh --no-pull  rebuild what's checked out (e.g. after a
#                                 manual git checkout for a rollback)
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE=fantasy-assist
IMAGE=fantasy-assist
KEEP_BACKUPS=5
HEALTH_TIMEOUT=120          # seconds; start-period is 30s, interval 60s

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31m!!  %s\033[0m\n' "$*" >&2; exit 1; }

pull=1
[[ "${1:-}" == "--no-pull" ]] && pull=0

if (( pull )); then
  branch=$(git branch --show-current)
  [[ "$branch" == main ]] || die "checkout is on '${branch:-detached HEAD}', not main (git switch main)"
  # config/ is meant to be edited on the server; anything else edited
  # locally would be silently built into the image, so stop
  if [[ -n "$(git status --porcelain --untracked-files=no -- . ':!config')" ]]; then
    git status --short -- . ':!config'
    die "local edits outside config/; commit, stash or git checkout them first"
  fi
fi

old_rev=$(git rev-parse --short HEAD)

# 1. Online DB backup through SQLite's backup API (consistent even mid-WAL,
#    no need to stop the app). Skipped on a first install.
if docker compose ps --status running -q "$SERVICE" | grep -q .; then
  say "Backing up data/hockey.db"
  docker compose exec -T "$SERVICE" python - "$old_rev" "$KEEP_BACKUPS" <<'PY'
import datetime, pathlib, sqlite3, sys
rev, keep = sys.argv[1], int(sys.argv[2])
src = pathlib.Path("/app/data/hockey.db")
if src.exists():
    out = src.parent / "backups"
    out.mkdir(exist_ok=True)
    dst = out / f"hockey-{datetime.date.today()}-{rev}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    for old in sorted(out.glob("hockey-*.db"), key=lambda p: p.stat().st_mtime)[:-keep]:
        old.unlink()
    print(f"    {dst.relative_to(src.parent.parent)}")
PY
  # keep the running image so a bad build can be rolled back instantly
  docker tag "$IMAGE:latest" "$IMAGE:previous" 2>/dev/null || true
fi

# 2. Pull + rebuild
if (( pull )); then
  say "Pulling"
  git pull --ff-only
fi
new_rev=$(git rev-parse --short HEAD)
say "Building $new_rev (was $old_rev)"
docker compose build --pull
docker compose up -d

# 3. Wait for the container's own health check
say "Waiting for healthy (up to ${HEALTH_TIMEOUT}s)"
deadline=$(( SECONDS + HEALTH_TIMEOUT ))
state=starting
while (( SECONDS < deadline )); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$SERVICE" 2>/dev/null || echo missing)
  [[ "$state" == healthy || "$state" == unhealthy ]] && break
  sleep 3
done

if [[ "$state" != healthy ]]; then
  docker compose logs --tail 40 "$SERVICE" || true
  if docker image inspect "$IMAGE:previous" >/dev/null 2>&1; then
    say "New build is '$state' -- rolling back to the previous image"
    docker tag "$IMAGE:previous" "$IMAGE:latest"
    docker compose up -d --no-build
    die "rolled back to the image from $old_rev; code is still at $new_rev (git checkout $old_rev && $0 --no-pull to match)"
  fi
  die "container is '$state' and there's no previous image to roll back to"
fi

docker image prune -f >/dev/null
docker compose ps
say "Updated $old_rev -> $new_rev"

# 4. systemd runs its own copy of the refresh units
if [[ "$old_rev" != "$new_rev" ]] && ! git diff --quiet "$old_rev" "$new_rev" -- deploy/fantasy-refresh.*; then
  cat <<EOF

The refresh timer/service changed. Re-install them:
  sudo cp deploy/fantasy-refresh.service deploy/fantasy-refresh.timer /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl restart fantasy-refresh.timer
EOF
fi
