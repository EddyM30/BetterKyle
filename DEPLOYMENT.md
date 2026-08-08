# BetterKyle deployment on Oracle Ubuntu

This guide assumes the Oracle Ubuntu user runs the existing case-sensitive
`BetterKyle.service` from `$HOME/BetterKyle`.

- GitHub fork: `https://github.com/EddyM30/BetterKyle.git`
- Python environment: `$HOME/BetterKyle/env` (not `.venv`)
- Database: `$HOME/BetterKyle/users.db`
- Secrets: `$HOME/BetterKyle/.env`

Run these commands as the same Ubuntu user that owns BetterKyle. Never delete
the existing database or replace `.env` with `.env.example`.

## 1. Push the fork

From your local checkout:

```bash
git push origin main
```

## 2. Prepare the Oracle server

SSH into the instance and run:

```bash
export BETTERKYLE_DIR="$HOME/BetterKyle"
export BETTERKYLE_USER="$(id -un)"
export BETTERKYLE_SERVICE="BetterKyle.service"
export BACKUP_DIR="$HOME/BetterKyle-backups/$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

test -d "$BETTERKYLE_DIR/.git"
test -s "$BETTERKYLE_DIR/.env"
test -f "$BETTERKYLE_DIR/users.db"
sudo systemctl cat "$BETTERKYLE_SERVICE"
git -C "$BETTERKYLE_DIR" status --short
```

If `git status` shows tracked production changes, stop and save them before
pulling. Do not use `git reset --hard`.

## 3. Back up the existing installation

```bash
git -C "$BETTERKYLE_DIR" rev-parse HEAD > "$BACKUP_DIR/old-commit.txt"
sudo systemctl cat "$BETTERKYLE_SERVICE" > "$BACKUP_DIR/BetterKyle.service"
cp -p "$BETTERKYLE_DIR/.env" "$BACKUP_DIR/.env"

tar --exclude='.git' --exclude='.env' --exclude='users.db*' \
  --exclude='env' --exclude='lavalink/Lavalink.jar' \
  --exclude='lavalink/plugins' --exclude='lavalink/logs' \
  -czf "$BACKUP_DIR/BetterKyle-code.tar.gz" \
  -C "$BETTERKYLE_DIR" .

sudo systemctl stop "$BETTERKYLE_SERVICE"
sqlite3 "$BETTERKYLE_DIR/users.db" \
  ".backup '$BACKUP_DIR/users.db'"
sqlite3 "$BACKUP_DIR/users.db" "PRAGMA integrity_check;"
```

The integrity check must print `ok`. Keep this backup until deployment is
stable.

## 4. Pull the GitHub fork

The ignored `.env` and `users.db` remain in place.

```bash
git -C "$BETTERKYLE_DIR" remote set-url origin \
  https://github.com/EddyM30/BetterKyle.git
git -C "$BETTERKYLE_DIR" fetch origin main
git -C "$BETTERKYLE_DIR" pull --ff-only origin main
git -C "$BETTERKYLE_DIR" log -1 --oneline
```

If `pull --ff-only` refuses because the server has local commits or changes,
stop and resolve them manually. Do not force the pull.

## 5. Install packages and create `env`

Oracle Ubuntu images commonly provide Python 3.10+. BetterKyle requires Python
3.10 or newer.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip \
  openjdk-17-jre-headless curl sqlite3

python3 --version
java -version

if [ ! -x "$BETTERKYLE_DIR/env/bin/python" ]; then
  python3 -m venv "$BETTERKYLE_DIR/env"
fi

"$BETTERKYLE_DIR/env/bin/python" -m pip install --upgrade pip
"$BETTERKYLE_DIR/env/bin/python" -m pip install \
  -r "$BETTERKYLE_DIR/requirements.txt"
"$BETTERKYLE_DIR/env/bin/python" -m pip check
```

Do not create `.venv`; this deployment intentionally uses `env`.

## 6. Preserve and update `.env`

Edit the existing file:

```bash
sudoedit "$BETTERKYLE_DIR/.env"
```

Keep the existing values and ensure these names exist:

```dotenv
DISCORD_TOKEN=<existing-value>
GUILD_ID=<existing-value>
CHANNEL_ID=<existing-value>
RIOT_API_KEY=<existing-value>
LAVALINK_URI=http://127.0.0.1:2333
LAVALINK_PASSWORD=<new-random-password>
SPOTIFY_CLIENT_ID=<spotify-client-id>
SPOTIFY_CLIENT_SECRET=<spotify-client-secret>
```

`LAVALINK_PASSWORD` must match Lavalink. Spotify values are needed for
Spotify URLs. Do not add a radio URL variable; LIVE 105 is already defined in
`music/radio_stations.py`.

## 7. Install Lavalink

Lavalink stays on loopback; no Oracle security-list port needs to be opened.

```bash
mkdir -p "$BETTERKYLE_DIR/lavalink"

curl --fail --location --proto '=https' --tlsv1.2 \
  -o "$BETTERKYLE_DIR/lavalink/Lavalink.jar.new" \
  https://github.com/lavalink-devs/Lavalink/releases/download/4.2.2/Lavalink.jar

test -s "$BETTERKYLE_DIR/lavalink/Lavalink.jar.new"
if [ -f "$BETTERKYLE_DIR/lavalink/Lavalink.jar" ]; then
  cp -p "$BETTERKYLE_DIR/lavalink/Lavalink.jar" \
    "$BACKUP_DIR/Lavalink.jar.old"
fi
mv "$BETTERKYLE_DIR/lavalink/Lavalink.jar.new" \
  "$BETTERKYLE_DIR/lavalink/Lavalink.jar"
mkdir -p "$BETTERKYLE_DIR/lavalink/plugins" \
  "$BETTERKYLE_DIR/lavalink/logs"
```

The tracked `lavalink/application.yml` configures Lavalink 4.2.2, LavaSrc
4.8.3, Spotify metadata, SoundCloud, and direct HTTP radio. LavaSrc downloads
into `lavalink/plugins` on first start.

## 8. Start Lavalink

```bash
sudo cp "$BETTERKYLE_DIR/deploy/lavalink.service.example" \
  /etc/systemd/system/lavalink.service

sudo sed -i \
  -e "s|<BETTERKYLE_USER>|$BETTERKYLE_USER|g" \
  -e "s|/PATH/TO/BetterKyle|$BETTERKYLE_DIR|g" \
  /etc/systemd/system/lavalink.service

sudo systemctl daemon-reload
sudo systemctl enable lavalink.service
sudo systemctl start lavalink.service
sudo systemctl status lavalink.service --no-pager
```

Check the local API without putting the password in shell history:

```bash
read -r -s -p "Lavalink password: " LAVALINK_CHECK_PASSWORD
printf '\n'

curl --fail --silent --show-error \
  -H "Authorization: $LAVALINK_CHECK_PASSWORD" \
  http://127.0.0.1:2333/v4/info

unset LAVALINK_CHECK_PASSWORD
sudo journalctl -u lavalink.service -n 100 --no-pager
```

## 9. Point `BetterKyle.service` at `env`

Inspect the existing unit:

```bash
sudo systemctl cat "$BETTERKYLE_SERVICE"
```

Keep its existing user, working directory, restart policy, and other settings.
Change only the Python executable so `ExecStart` uses:

```text
$HOME/BetterKyle/env/bin/python
```

If it still points at `.venv`, edit the existing unit:

```bash
sudoedit /etc/systemd/system/BetterKyle.service
sudo systemctl daemon-reload
```

Then start BetterKyle:

```bash
sudo systemctl start "$BETTERKYLE_SERVICE"
sudo systemctl status "$BETTERKYLE_SERVICE" --no-pager
sudo journalctl -u "$BETTERKYLE_SERVICE" -n 150 --no-pager
```

The logs should show Discord connection and guild command sync. If Lavalink is
unavailable, League tracking must still continue.

## 10. Quick verification

In Discord, verify:

- `/riot link`, `/unlink`, and `/refresh`
- `/play`, `/queue`, `/nowplaying`, `/skip`, `/stop`
- `/shuffle`, `/volume`, `/disconnect`, `/clearqueue`, `/remove`
- `/pause` and `/resume`
- `/radio live105`
- switching between LIVE 105 and `/play`
- existing linked accounts and match history

The tracker accepts only ARAM, Ranked Solo/Duo, Ranked Flex, Normal Draft, and
Swiftplay. Unsupported event modes must not post.

## Rollback

If the new version fails:

```bash
sudo systemctl stop "$BETTERKYLE_SERVICE"
sudo systemctl stop lavalink.service

git -C "$BETTERKYLE_DIR" switch --detach \
  "$(cat "$BACKUP_DIR/old-commit.txt")"

cp -p "$BACKUP_DIR/.env" "$BETTERKYLE_DIR/.env"
sqlite3 "$BACKUP_DIR/users.db" \
  ".backup '$BETTERKYLE_DIR/users.db.rollback'"
mv "$BETTERKYLE_DIR/users.db.rollback" "$BETTERKYLE_DIR/users.db"

sudo systemctl start "$BETTERKYLE_SERVICE"
```

If the old service used a different Python path, restore the saved unit from
`$BACKUP_DIR/BetterKyle.service` before starting it. Keep the failed checkout,
database, `.env`, and backup directory; do not delete them.
