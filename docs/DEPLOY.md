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
from Yahoo. A failure (NHL or Yahoo down) shows up as a failed unit in the
journal; the app keeps serving the previous data.

## 3. Updating to a new version

```bash
~/fantasy-assist/deploy/update.sh      # git pull + rebuild + restart
```

## 4. Nice-to-haves with what you already run

- **Uptime Kuma:** add an HTTP monitor for
  `http://marshall-home:8420/api/inseason/leagues` (the same URL the
  container's health check uses).
- **Homepage dashboard:** add a service tile pointing to
  `http://marshall-home:8420/inseason`.
- **Restic / B2:** include `~/fantasy-assist/data` (the database and your
  lists) and `~/fantasy-assist/config` in the backup set.

## Notes

- Port: the container listens on 8000; compose maps it to **8420** on the
  host. Change the left side of `"8420:8000"` in `docker-compose.yml` if
  that port is taken.
- Settings: edit `config/inseason.yaml` (Yahoo league/team id, model knobs)
  or a league YAML on the server, then `docker compose restart`.
- The container runs as uid 1000, so files it writes in `data/` belong to
  your user.
- Time zone is America/New_York (container `TZ`), so "today" and the
  schedule windows line up with NHL dates.
