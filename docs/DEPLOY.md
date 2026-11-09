# Running on the home server (marshall-home)

The app runs as one Docker container: draft board at `/`, mid-season
assistant at `/inseason`. A systemd timer refreshes the data twice a day.
Your data (`data/`) and settings (`config/`) are folders on the server, so
rebuilding or updating the container never loses them.

> **Keep it private.** There is no login, and *Refresh data* / *Lists* change
> data. Reach it on the LAN or over Tailscale only — never add an OPNsense
> port forward for it.

## 1. First install

On the ZimaBoard:

```bash
cd ~
git clone https://github.com/KyleSocks/fantasy-assist.git
cd fantasy-assist
mkdir -p data
```

Bring over the database you already have, from the Windows PC (PowerShell),
so you skip the multi-minute history ingest:

```powershell
scp "C:\AI_Space\Fantasy Hockey Assistant\data\hockey.db" <you>@marshall-home:~/fantasy-assist/data/
```

Then start it:

```bash
docker compose up -d --build
docker compose ps          # STATUS should become "healthy" within a minute
```

Open **http://marshall-home:8420/inseason** (or `http://192.168.10.69:8420`
on the LAN, or the Tailscale name from anywhere) and press **Refresh data**.

*Starting from an empty database instead?* Ingest the completed seasons, then
press Refresh data for the current one:

```bash
docker compose exec fantasy-assist python -m src.ingest.ingest \
    --season-ids 20232024,20242025,20252026
```

## 2. Automatic refresh (06:30 and 14:30 Eastern)

```bash
sudo cp deploy/fantasy-refresh.service deploy/fantasy-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fantasy-refresh.timer
systemctl list-timers fantasy-refresh.timer      # next run time
```

Run one now / check the last run:

```bash
sudo systemctl start fantasy-refresh.service
journalctl -u fantasy-refresh.service -n 20
```

The refresh is the same as the page's button: this season's stats, rosters,
the schedule, your roster, every available player and all 12 league rosters
from Yahoo. A failure (NHL or Yahoo down, or the Yahoo half skipped -- the response
then carries a `yahoo_error`) shows up as a failed unit in the journal;
the app keeps serving the previous data. Transient errors are retried
first, so a failed unit means a real outage or a changed Yahoo page.

## 3. Updating to a new version

```bash
cd ~/fantasy-assist && ./deploy/update.sh
```

That one command:

1. refuses to run unless the checkout is on `main` with no local edits outside `config/`
   (your own settings tweaks there are fine);
2. backs up the database while the app keeps running (SQLite's online
   backup) to `data/backups/hockey-<date>-<commit>.db`, keeping the last 5;
3. pulls, rebuilds (with a fresh base image, for security patches) and
   restarts;
4. waits for the container to report **healthy** -- if it doesn't within
   two minutes, it prints the logs and puts the previous image back, so the
   app keeps working while you look into it;
5. tells you if the refresh timer/service changed, with the two commands
   to re-install them (systemd runs its own copy in /etc/systemd/system).

**Rolling back by hand:** `git log --oneline -5`, `git checkout <commit>`,
then `./deploy/update.sh --no-pull`. If the newer version changed the
database, stop the app and copy a file from `data/backups/` over
`data/hockey.db` first. `git switch main` returns to normal updates.

## 4. Nice-to-haves with what you already run

- **Uptime Kuma:** add an HTTP monitor for
  `http://marshall-home:8420/api/inseason/leagues` (the same URL the
  container's health check uses).
- **Homepage dashboard:** add a service tile pointing to
  `http://marshall-home:8420/inseason`.
- **Restic / B2:** include `~/fantasy-assist/data` (the database, your
  lists and `data/backups/`) and `~/fantasy-assist/config` in the backup set.

## Notes

- Hostnames: the app only answers to IP addresses, `localhost` and the
  names in `FA_ALLOWED_HOSTS` in `docker-compose.yml` (`marshall-home`, its
  Tailscale MagicDNS name, and the container name for other containers).
  That blocks DNS-rebinding pages, which could otherwise read and change your
  lists from any site open in your browser. If you open it by some other name
  and get "Invalid host header", add that name there and `docker compose up -d`.
- Port: the container listens on 8000; compose maps it to **8420** on the
  host. Change the left side of `"8420:8000"` in `docker-compose.yml` if
  that port is taken.
- Settings: edit `config/inseason.yaml` (Yahoo league/team id, model knobs)
  or a league YAML on the server. The app notices the change on the next
  request; no restart needed. (The container sees `config/` read-only, so
  edit it on the host, not inside the container.)
- The container runs as uid 1000 with a read-only filesystem: `data/` is
  the only place it can write, so it must belong to uid 1000
  (`ls -ln data`; fix with `sudo chown -R 1000:1000 data`).
- A refresh while another is running answers "a refresh is already
  running" instead of starting a second one.
- Time zone is America/New_York (container `TZ`), so "today" and the
  schedule windows line up with NHL dates.
