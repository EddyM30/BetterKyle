# BetterKyle Ubuntu deployment

This guide replaces the older production checkout with a tested commit from this
fork. It deliberately preserves the existing BetterKyle systemd service, `.env`,
and SQLite state. Run these commands on the Ubuntu server only when a deployment
window has been scheduled; none of them are needed on the local development copy.

The deployment described here uses:

- Python 3.12
- Lavalink 4.2.2
- OpenJDK 17 or newer (OpenJDK 17 is the documented baseline below)
- LavaSrc 4.8.3, downloaded by Lavalink's plugin manager
- the pinned Python packages in `requirements.txt`

Upstream references: [Lavalink 4.2.2 release](https://github.com/lavalink-devs/Lavalink/releases/tag/4.2.2),
[Lavalink v4 requirements](https://lavalink.dev/changelog/v4), and
[LavaSrc 4.8.3 release](https://github.com/topi314/LavaSrc/releases/tag/4.8.3).

Lavalink listens only on `127.0.0.1:2333`. Do not expose that port to the
internet.

## 1. Set deployment placeholders

First inspect the existing unit and fill in the real values. The virtual
environment path must be the path already used by the BetterKyle unit so the
unit itself can remain unchanged. If the old service does not use a virtual
environment, create one at `<BETTERKYLE_DIR>/.venv` and update only its
`ExecStart` Python path during the maintenance window.

```bash
sudo systemctl cat BetterKyle.service
sudo systemctl show BetterKyle.service -p User -p WorkingDirectory -p ExecStart
```

The unit name is case-sensitive. Replace `BetterKyle.service` below if the
existing production service has a different name.

```bash
export BETTERKYLE_DIR="/PATH/TO/BetterKyle"
export BETTERKYLE_USER="<BETTERKYLE_USER>"
export BETTERKYLE_SERVICE="BetterKyle.service"
export BETTERKYLE_VENV="/PATH/TO/BetterKyle/.venv"
export BETTERKYLE_DB_SOURCE="/ACTUAL/CURRENT/PATH/users.db"
export BETTERKYLE_DB_TARGET="/PATH/TO/BetterKyle/users.db"
export BETTERKYLE_BACKUP_ROOT="/PATH/TO/BETTERKYLE_BACKUPS"
export TARGET_COMMIT="<NEW_TESTED_COMMIT>"
export DEPLOY_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export BETTERKYLE_BACKUP_DIR="${BETTERKYLE_BACKUP_ROOT}/${DEPLOY_ID}"
```

Keep this shell open through the deployment. Record `DEPLOY_ID` and
`BETTERKYLE_BACKUP_DIR`; the rollback commands use both.

`BETTERKYLE_DB_SOURCE` must name the database the old running service actually
uses. Older BetterKyle versions opened the relative path `users.db`, so inspect
the old unit's `WorkingDirectory` and the deployed `database.py` rather than
assuming it is the repository root. `BETTERKYLE_DB_TARGET` must be
`$BETTERKYLE_DIR/users.db`, because the new implementation deliberately anchors
the database there. Use canonical absolute paths. In the usual deployment both
variables have the same value.

Perform the preflight checks before changing anything:

```bash
test -d "$BETTERKYLE_DIR/.git"
test -s "$BETTERKYLE_DIR/.env"
test -s "$BETTERKYLE_DB_SOURCE"
git -C "$BETTERKYLE_DIR" status --short
sudo systemctl status "$BETTERKYLE_SERVICE" --no-pager
```

An ignored `.env` and `users.db` will not appear in `git status`; that is
expected. Resolve unexplained changes to tracked production files before
continuing. Do not discard them.

## 2. Back up the current production installation

Install the small command-line tools used by the backup and download steps:

```bash
sudo apt-get update
sudo apt-get install --yes ca-certificates curl sqlite3
mkdir -p "$BETTERKYLE_BACKUP_DIR"
chmod 700 "$BETTERKYLE_BACKUP_DIR"
```

Record the exact working version, repository state, service definition, and a
Git bundle. The worktree archive also captures untracked production code while
excluding secrets, the live database, virtual environments, and generated
Lavalink files; those are handled separately.

```bash
git -C "$BETTERKYLE_DIR" rev-parse HEAD | tee "$BETTERKYLE_BACKUP_DIR/current-commit.txt"
git -C "$BETTERKYLE_DIR" branch --show-current | tee "$BETTERKYLE_BACKUP_DIR/current-branch.txt"
git -C "$BETTERKYLE_DIR" status --short > "$BETTERKYLE_BACKUP_DIR/worktree-status.txt"
git -C "$BETTERKYLE_DIR" diff --binary HEAD > "$BETTERKYLE_BACKUP_DIR/tracked-changes.patch"
git -C "$BETTERKYLE_DIR" ls-files --others --exclude-standard > "$BETTERKYLE_BACKUP_DIR/untracked-files.txt"
git -C "$BETTERKYLE_DIR" bundle create "$BETTERKYLE_BACKUP_DIR/repository.bundle" --all
sudo systemctl cat "$BETTERKYLE_SERVICE" > "$BETTERKYLE_BACKUP_DIR/betterkyle-service.txt"
printf '%s\n' "$BETTERKYLE_DB_SOURCE" > "$BETTERKYLE_BACKUP_DIR/database-source-path.txt"
tar \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='*.db' \
  --exclude='*.db-journal' \
  --exclude='*.db-shm' \
  --exclude='*.db-wal' \
  --exclude='./.venv*' \
  --exclude='./lavalink/Lavalink.jar' \
  --exclude='./lavalink/plugins' \
  --exclude='./lavalink/logs' \
  -czf "$BETTERKYLE_BACKUP_DIR/worktree.tar.gz" \
  -C "$BETTERKYLE_DIR" .
install -m 600 "$BETTERKYLE_DIR/.env" "$BETTERKYLE_BACKUP_DIR/.env"
```

Use SQLite's online backup command rather than copying a database that the bot
may be writing. This captures the linked Riot accounts, match cursors,
deduplication history, and streaks in one consistent file.

```bash
sqlite3 "$BETTERKYLE_DB_SOURCE" ".backup '$BETTERKYLE_BACKUP_DIR/users.pre-stop.db'"
sqlite3 "$BETTERKYLE_BACKUP_DIR/users.pre-stop.db" "PRAGMA integrity_check;"
sqlite3 "$BETTERKYLE_BACKUP_DIR/users.pre-stop.db" \
  "SELECT 'users', COUNT(*) FROM users UNION ALL SELECT 'matches', COUNT(*) FROM matches;" \
  | tee "$BETTERKYLE_BACKUP_DIR/pre-stop-row-counts.txt"
sha256sum "$BETTERKYLE_BACKUP_DIR/users.pre-stop.db" "$BETTERKYLE_BACKUP_DIR/.env" \
  > "$BETTERKYLE_BACKUP_DIR/pre-stop-sha256.txt"
```

`PRAGMA integrity_check` must print `ok`. Stop here if it does not.

## 3. Stop BetterKyle and take the final database backup

Do not stop or replace any unrelated service. Stop only the existing bot, then
take a final backup so no match update can land between the backup and cutover.

```bash
sudo systemctl stop "$BETTERKYLE_SERVICE"
sudo systemctl status "$BETTERKYLE_SERVICE" --no-pager
sqlite3 "$BETTERKYLE_DB_SOURCE" ".backup '$BETTERKYLE_BACKUP_DIR/users.db'"
sqlite3 "$BETTERKYLE_BACKUP_DIR/users.db" "PRAGMA integrity_check;"
sqlite3 "$BETTERKYLE_BACKUP_DIR/users.db" \
  "SELECT 'users', COUNT(*) FROM users UNION ALL SELECT 'matches', COUNT(*) FROM matches;" \
  | tee "$BETTERKYLE_BACKUP_DIR/final-row-counts.txt"
sha256sum "$BETTERKYLE_BACKUP_DIR/users.db" > "$BETTERKYLE_BACKUP_DIR/final-database-sha256.txt"
```

Again, require `ok` before proceeding. Never delete or recreate the production
database.

The new code always opens `$BETTERKYLE_DB_TARGET`. If the old service used a
different location, preserve any file already at the target and populate the
target from the verified final backup. The original source database remains
untouched for rollback.

```bash
if [ "$BETTERKYLE_DB_SOURCE" != "$BETTERKYLE_DB_TARGET" ]; then
  if [ -e "$BETTERKYLE_DB_TARGET" ]; then
    mv "$BETTERKYLE_DB_TARGET" \
      "$BETTERKYLE_BACKUP_DIR/users.target-before-migration.db"
  fi
  sqlite3 "$BETTERKYLE_BACKUP_DIR/users.db" ".backup '$BETTERKYLE_DB_TARGET'"
  sudo chown "${BETTERKYLE_USER}:" "$BETTERKYLE_DB_TARGET"
  sudo chmod 600 "$BETTERKYLE_DB_TARGET"
  sqlite3 "$BETTERKYLE_DB_TARGET" "PRAGMA integrity_check;"
fi
```

## 4. Deploy the tested code commit

The tested commit must first be available to the Ubuntu checkout, normally by
pushing it to a remote the server can read. The following approach leaves the
checkout detached at an exact immutable commit, which makes the deployed version
unambiguous.

```bash
git -C "$BETTERKYLE_DIR" status --porcelain --untracked-files=no
git -C "$BETTERKYLE_DIR" fetch --prune origin
git -C "$BETTERKYLE_DIR" cat-file -e "${TARGET_COMMIT}^{commit}"
git -C "$BETTERKYLE_DIR" switch --detach "$TARGET_COMMIT"
git -C "$BETTERKYLE_DIR" rev-parse HEAD | tee "$BETTERKYLE_BACKUP_DIR/deployed-commit.txt"
```

The first command must produce no tracked changes. If it does, stop and reconcile
those changes against the worktree backup rather than forcing the checkout.

Confirm that Git left the two protected runtime files in place and that the new
checkout contains the expected configuration:

```bash
test -s "$BETTERKYLE_DIR/.env"
test -s "$BETTERKYLE_DB_TARGET"
test -f "$BETTERKYLE_DIR/.env.example"
test -f "$BETTERKYLE_DIR/lavalink/application.yml"
test -f "$BETTERKYLE_DIR/deploy/lavalink.service.example"
test -f "$BETTERKYLE_DIR/music/radio_stations.py"
sqlite3 "$BETTERKYLE_DB_TARGET" "PRAGMA integrity_check;"
```

## 5. Preserve and extend `.env`

Edit the existing production `.env`; do not replace it with `.env.example`.
Keep all four existing values and append the four music values.

Existing BetterKyle variables:

| Variable | Purpose | Required |
| --- | --- | --- |
| `DISCORD_TOKEN` | Discord bot token | Yes |
| `GUILD_ID` | The one Discord server where commands are registered | Yes |
| `CHANNEL_ID` | Channel used for League match announcements | Yes |
| `RIOT_API_KEY` | Riot API authentication | Yes |

New music/Lavalink variables:

| Variable | Purpose | Required |
| --- | --- | --- |
| `LAVALINK_URI` | Wavelink connection URL; use `http://127.0.0.1:2333` for the local service | Set explicitly in production; this is also the code default |
| `LAVALINK_PASSWORD` | Shared secret used by Wavelink and Lavalink | Yes for music/radio |
| `SPOTIFY_CLIENT_ID` | Spotify application client ID consumed by LavaSrc | Yes for Spotify URLs |
| `SPOTIFY_CLIENT_SECRET` | Spotify application secret consumed by LavaSrc | Yes for Spotify URLs |

Generate a unique Lavalink password and put it in `.env`; do not reuse a Discord,
Riot, or Spotify secret.

```bash
openssl rand -hex 32
sudoedit "$BETTERKYLE_DIR/.env"
sudo chown "${BETTERKYLE_USER}:" "$BETTERKYLE_DIR/.env"
sudo chmod 600 "$BETTERKYLE_DIR/.env"
```

Use plain `KEY=value` entries, with no `export` prefix:

```dotenv
DISCORD_TOKEN=<preserve-existing-value>
GUILD_ID=<preserve-existing-value>
CHANNEL_ID=<preserve-existing-value>
RIOT_API_KEY=<preserve-existing-value>
LAVALINK_URI=http://127.0.0.1:2333
LAVALINK_PASSWORD=<new-random-password>
SPOTIFY_CLIENT_ID=<spotify-client-id>
SPOTIFY_CLIENT_SECRET=<spotify-client-secret>
```

The same `.env` is read directly by BetterKyle and by the Lavalink systemd unit.
`LAVALINK_PASSWORD` therefore automatically matches the placeholder in
`lavalink/application.yml`.

Riot queue IDs are not environment variables. The queue allowlist is
source-controlled in `league/queues.py`.

### Spotify credentials

Create an application in the Spotify Developer Dashboard and copy its client ID
and client secret into the production `.env`. LavaSrc uses those credentials for
Spotify metadata. BetterKyle does not scrape or download Spotify audio: it
resolves the returned metadata to playable SoundCloud results.

Never commit the populated `.env`, paste it into logs, or put the credentials in
`lavalink/application.yml`.

### LIVE 105 needs no environment variable

LIVE 105 is already defined in the source-controlled station registry at
`music/radio_stations.py`:

```text
Key: live105
Name: LIVE 105
Frequency: 105.3
Callsign: KITS
Stream type: AAC/live HTTP stream
URL: https://live.amperwave.net/direct/audacy-kitsfmaac-imc
```

Do not add a `LIVE105_URL` or other radio URL to `.env`.

## 6. Create the Python 3.12 virtual environment

Ubuntu 24.04 provides Python 3.12 in its standard repositories. On another
Ubuntu release, first confirm that `apt-cache policy python3.12` shows an
approved candidate; do not silently fall back to an older interpreter. If it
does not, upgrade the host or install Python 3.12 through the server's approved
package source before continuing.

```bash
sudo apt-get update
sudo apt-get install --yes python3.12 python3.12-venv
python3.12 --version
```

Keep the old environment beside the new one so it can be restored without a
download. The new environment must be created at its final path because virtual
environment scripts contain absolute paths.

```bash
if [ -d "$BETTERKYLE_VENV" ]; then
  mv "$BETTERKYLE_VENV" "${BETTERKYLE_VENV}.pre-${DEPLOY_ID}"
fi
python3.12 -m venv "$BETTERKYLE_VENV"
"$BETTERKYLE_VENV/bin/python" -m pip install --upgrade pip
"$BETTERKYLE_VENV/bin/python" -m pip install --requirement "$BETTERKYLE_DIR/requirements.txt"
"$BETTERKYLE_VENV/bin/python" -m pip check
"$BETTERKYLE_VENV/bin/python" -c \
  "import aiohttp, aiosqlite, discord, dotenv, wavelink; print('Python imports OK')"
```

Run local deterministic checks before starting production:

```bash
cd "$BETTERKYLE_DIR"
"$BETTERKYLE_VENV/bin/python" -m compileall -q bot.py config.py database.py league music
"$BETTERKYLE_VENV/bin/python" -m unittest discover -s tests -v
"$BETTERKYLE_VENV/bin/python" -c \
  "from config import validate_core_settings; validate_core_settings(); print('Core configuration OK')"
```

Do not continue if compilation, tests, imports, package checks, or core
configuration validation fail.

## 7. Install Java 17

Lavalink 4.2.2 requires Java 17 or newer. Install the headless OpenJDK 17 runtime
and verify that `/usr/bin/java`, which the service example uses, resolves to at
least version 17.

```bash
sudo apt-get update
sudo apt-get install --yes openjdk-17-jre-headless
java -version
readlink -f /usr/bin/java
```

If another installed JRE leaves `/usr/bin/java` below version 17, select the
OpenJDK 17 alternative and run `java -version` again:

```bash
sudo update-alternatives --config java
```

## 8. Install and configure Lavalink 4.2.2

The JAR and `application.yml` belong together in
`$BETTERKYLE_DIR/lavalink/`. `application.yml` is already tracked; the JAR,
downloaded plugins, and logs are intentionally ignored by Git.

Download the pinned official release to a temporary filename, then rename it
only after a complete transfer:

```bash
curl --fail --location --proto '=https' --tlsv1.2 \
  --output "$BETTERKYLE_DIR/lavalink/Lavalink.jar.download" \
  https://github.com/lavalink-devs/Lavalink/releases/download/4.2.2/Lavalink.jar
test -s "$BETTERKYLE_DIR/lavalink/Lavalink.jar.download"
mv "$BETTERKYLE_DIR/lavalink/Lavalink.jar.download" \
  "$BETTERKYLE_DIR/lavalink/Lavalink.jar"
sha256sum "$BETTERKYLE_DIR/lavalink/Lavalink.jar" \
  | tee "$BETTERKYLE_BACKUP_DIR/Lavalink-4.2.2.sha256"
sudo chown -R "${BETTERKYLE_USER}:" "$BETTERKYLE_DIR/lavalink"
sudo -u "$BETTERKYLE_USER" mkdir -p \
  "$BETTERKYLE_DIR/lavalink/logs" \
  "$BETTERKYLE_DIR/lavalink/plugins"
```

The checked-in `lavalink/application.yml` configures:

- the password from `${LAVALINK_PASSWORD}`;
- LavaSrc dependency `com.github.topi314.lavasrc:lavasrc-plugin:4.8.3`;
- Spotify metadata using `${SPOTIFY_CLIENT_ID}` and
  `${SPOTIFY_CLIENT_SECRET}`;
- SoundCloud playback and `scsearch` fallback resolution;
- the native HTTP source manager needed for direct HTTP/HTTPS radio streams;
- a loopback-only server on port 2333.

Lavalink downloads LavaSrc 4.8.3 into `lavalink/plugins/` on first start, so the
service user needs write access there and outbound HTTPS access to
`maven.lavalink.dev`. No separate LavaSrc JAR should be copied manually.

## 9. Install the Lavalink systemd service

Use the tracked example as the starting point:

```bash
if [ -e /etc/systemd/system/lavalink.service ]; then
  sudo cp --preserve /etc/systemd/system/lavalink.service \
    "$BETTERKYLE_BACKUP_DIR/lavalink.service.before-deployment"
fi
sudo install -m 0644 \
  "$BETTERKYLE_DIR/deploy/lavalink.service.example" \
  /etc/systemd/system/lavalink.service
sudoedit /etc/systemd/system/lavalink.service
```

Replace both occurrences of `/PATH/TO/BetterKyle` with the absolute production
path and replace `<BETTERKYLE_USER>` with the same unprivileged user that runs
BetterKyle. The final service must have these effective paths:

```ini
[Service]
User=<BETTERKYLE_USER>
WorkingDirectory=/PATH/TO/BetterKyle/lavalink
EnvironmentFile=/PATH/TO/BetterKyle/.env
ExecStart=/usr/bin/java -jar /PATH/TO/BetterKyle/lavalink/Lavalink.jar
```

Do not run Lavalink as root. The service's working directory is important:
Lavalink discovers `application.yml` there and writes its `plugins/` and `logs/`
directories there.

Validate and start the new service. These commands do not alter the existing
BetterKyle service definition.

```bash
sudo systemd-analyze verify /etc/systemd/system/lavalink.service
sudo systemctl daemon-reload
sudo systemctl enable lavalink.service
sudo systemctl start lavalink.service
sudo systemctl status lavalink.service --no-pager
sudo journalctl -u lavalink.service -n 100 --no-pager
```

The first start can take longer while LavaSrc downloads. The log must show a
successful Lavalink start and LavaSrc 4.8.3 load, with no blank-credential or
YAML errors.

Check the authenticated API without putting the password in shell history:

```bash
read -r -s -p "Lavalink password: " LAVALINK_CHECK_PASSWORD
printf '\n'
curl --fail --silent --show-error \
  --header "Authorization: ${LAVALINK_CHECK_PASSWORD}" \
  http://127.0.0.1:2333/v4/info \
  | "$BETTERKYLE_VENV/bin/python" -m json.tool
unset LAVALINK_CHECK_PASSWORD
```

Confirm the response reports Lavalink `4.2.2`, a JVM of 17 or newer, a
SoundCloud source manager, and LavaSrc `4.8.3` in the plugin list.

## 10. Start BetterKyle

Re-check that the existing BetterKyle unit still points to the selected
`$BETTERKYLE_VENV/bin/python`, its existing `bot.py`, working directory, and
service user. Preserve all other production-specific unit settings.

```bash
sudo systemctl cat "$BETTERKYLE_SERVICE"
sudo systemctl daemon-reload
sudo systemctl start "$BETTERKYLE_SERVICE"
sudo systemctl status "$BETTERKYLE_SERVICE" --no-pager
sudo journalctl -u "$BETTERKYLE_SERVICE" -n 150 --no-pager
```

Look for successful Discord connection, guild command sync, and Lavalink node
connection. A Lavalink failure should produce a warning while BetterKyle and its
League subsystem remain running.

## 11. Exact recommended deployment order

1. Identify the actual checkout, active SQLite path, user, venv, and existing
   BetterKyle unit.
2. Record the old commit and dirty worktree state.
3. Back up the code, Git repository, service definition, `.env`, and SQLite with
   the bot still running.
4. Verify the pre-stop SQLite backup reports `ok` and record row counts.
5. Stop only BetterKyle.
6. Take and verify the final SQLite backup.
7. If the old database is not at the new repository-root path, populate that
   target from the verified backup while retaining the original database.
8. Fetch and switch to the exact tested commit without forcing over tracked
   production changes.
9. Confirm `.env` and `users.db` remain present and healthy.
10. Preserve existing `.env` values and add the Lavalink/Spotify values.
11. Create the Python 3.12 venv at the path used by the existing service and
    install `requirements.txt`.
12. Run package, import, compilation, test, and configuration checks.
13. Install and verify OpenJDK 17.
14. Download Lavalink 4.2.2 beside the tracked `application.yml`.
15. Install the service from `deploy/lavalink.service.example` and start
    Lavalink.
16. Verify Lavalink 4.2.2 and LavaSrc 4.8.3 through logs and `/v4/info`.
17. Start the existing BetterKyle service.
18. Verify the database, League commands/polling, music, and radio.
19. Keep the complete backup and old virtual environment until the deployment
    has been stable for an agreed retention period.

## 12. Production verification

### Database and startup

```bash
sqlite3 "$BETTERKYLE_DB_TARGET" "PRAGMA integrity_check;"
sqlite3 "$BETTERKYLE_DB_TARGET" \
  "SELECT 'users', COUNT(*) FROM users UNION ALL SELECT 'matches', COUNT(*) FROM matches;"
sudo systemctl is-active lavalink.service
sudo systemctl is-active "$BETTERKYLE_SERVICE"
sudo journalctl -u lavalink.service -n 100 --no-pager
sudo journalctl -u "$BETTERKYLE_SERVICE" -n 150 --no-pager
```

Require `ok`, compare the account/match counts with
`final-row-counts.txt`, and confirm both services are active. Existing linked
accounts, match IDs, last-match cursors, and streaks must remain.

### League

- Confirm `/riot link`, `/unlink`, and `/refresh` are registered in the intended
  guild. Do not relink an existing account merely as a smoke test.
- Run `/refresh` as an already linked user and confirm it completes without
  changing that link.
- Leave the bot running for longer than its two-minute polling interval and
  confirm polling remains healthy in the service log.
- Confirm the deterministic tests pass for the explicit supported queue IDs:
  ARAM `450`, Ranked Solo/Duo `420`, Ranked Flex `440`, Normal Draft `400`, and
  Swiftplay `480`.
- As real matches become available, confirm an embed is posted once for each
  supported queue and party members are grouped without duplicate posts.
- Confirm ARAM Mayhem, Arena, rotating/event modes, and other generic
  Classic-style queues do not post. These exclusions are based on queue ID, not
  map or `gameMode` text.
- Confirm streak state advances in SQLite and multi-kill/highlight output still
  appears on applicable results.

Some queue types cannot be exercised on demand during a short maintenance
window. Treat deterministic allowlist tests as the deployment gate and monitor
live results as those matches occur; do not manufacture production match state.

### Music

Before testing, ensure the bot role has Discord **Connect**, **Speak**, and
**Use Application Commands** permissions in the test voice/text channels.

- Confirm `/play`, `/radio`, `/pause`, `/resume`, `/skip`, `/stop`, `/queue`,
  `/nowplaying`, `/shuffle`, `/volume`, `/disconnect`, `/clearqueue`, and
  `/remove` are registered.
- From a voice channel, run `/play deftones change` and confirm text search
  resolves through SoundCloud.
- Test a SoundCloud track URL and a SoundCloud playlist/set URL.
- Test a Spotify track, album, and playlist URL. For a playlist, confirm the
  command defers promptly, preserves order, reports additions, and reports any
  tracks that could not be resolved instead of failing the whole playlist.
- Queue multiple tracks and verify queue display, skip, remove, clear, shuffle,
  pause/resume, stop, and automatic next-track playback.
- Verify `/volume 0` and `/volume 200` are accepted and values outside `0-200`
  are rejected.
- Verify a user in a different voice channel cannot unexpectedly move the bot.
- Run `/disconnect` and confirm the queue and player state are cleaned up.

### LIVE 105 radio

- Run `/radio live105` from a voice channel and confirm BetterKyle joins and
  audible LIVE 105 audio starts.
- Run `/nowplaying`; it must show LIVE 105, `105.3 KITS`, `Live Radio`, and
  `LIVE`, without a fake duration or fabricated song title.
- Run `/queue` and `/skip`; both must explain the live-radio state rather than
  pretending there is a next track.
- Run `/play` while radio is active and confirm it switches to normal music.
- Run `/radio live105` during normal music and confirm it stops music and clears
  the upcoming queue before starting radio.
- Run `/stop` and confirm the stream stops while the bot remains connected, then
  run `/disconnect` and confirm it leaves voice.
- If LIVE 105 is temporarily unreachable, confirm the command reports a clean
  error, radio state resets, and `/refresh` and League polling continue.

The checked-in node configuration resolved the LIVE 105 URL locally as a
non-seekable ADTS live stream. Audible AAC playback still depends on the
external endpoint, Discord voice, and the deployed Lavalink node, so it must be
verified on the production host; the deterministic test suite intentionally
does not require the station to be online.

### Optional subsystem-isolation check

During a controlled window with nobody listening, stop Lavalink, run `/refresh`,
and confirm League remains healthy. Then restore Lavalink:

```bash
sudo systemctl stop lavalink.service
sudo systemctl status "$BETTERKYLE_SERVICE" --no-pager
sudo systemctl start lavalink.service
sudo systemctl status lavalink.service --no-pager
```

## 13. Rollback without losing data

Rollback changes the code and Python environment, not the active database. New
music variables in `.env` are harmless to the old bot, so leave the current
`.env` in place unless it was actually damaged.

Re-establish the variables from section 1, using the original `DEPLOY_ID` and
backup directory, then load the recorded old commit:

```bash
export OLD_COMMIT="$(cat "$BETTERKYLE_BACKUP_DIR/current-commit.txt")"
sudo systemctl stop "$BETTERKYLE_SERVICE"
sudo systemctl stop lavalink.service
sqlite3 "$BETTERKYLE_DB_TARGET" \
  ".backup '$BETTERKYLE_BACKUP_DIR/users.failed-deployment.db'"
sqlite3 "$BETTERKYLE_BACKUP_DIR/users.failed-deployment.db" "PRAGMA integrity_check;"
install -m 600 "$BETTERKYLE_DIR/.env" "$BETTERKYLE_BACKUP_DIR/.env.failed-deployment"
git -C "$BETTERKYLE_DIR" status --porcelain --untracked-files=no
git -C "$BETTERKYLE_DIR" switch --detach "$OLD_COMMIT"
git -C "$BETTERKYLE_DIR" rev-parse HEAD
```

Do not force the Git switch if tracked files changed during deployment; inspect
and archive those changes first.

If the old service used a database location outside the repository root, copy
the latest verified post-deployment state back to that location before starting
the old code. Preserve the old source file, and build the replacement beside it
before the atomic rename:

```bash
if [ "$BETTERKYLE_DB_SOURCE" != "$BETTERKYLE_DB_TARGET" ]; then
  export ROLLBACK_DB_NEXT="${BETTERKYLE_DB_SOURCE}.rollback-${DEPLOY_ID}"
  sqlite3 "$BETTERKYLE_DB_TARGET" ".backup '$ROLLBACK_DB_NEXT'"
  sqlite3 "$ROLLBACK_DB_NEXT" "PRAGMA integrity_check;"
  sudo chown "${BETTERKYLE_USER}:" "$ROLLBACK_DB_NEXT"
  sudo chmod 600 "$ROLLBACK_DB_NEXT"
  sudo mv "$BETTERKYLE_DB_SOURCE" \
    "$BETTERKYLE_BACKUP_DIR/users.source-before-rollback.db"
  sudo mv "$ROLLBACK_DB_NEXT" "$BETTERKYLE_DB_SOURCE"
fi
```

Restore the prior virtual environment if section 6 moved one aside. Move the new
environment out of the way rather than deleting it:

```bash
if [ -d "${BETTERKYLE_VENV}.pre-${DEPLOY_ID}" ]; then
  mv "$BETTERKYLE_VENV" "${BETTERKYLE_VENV}.failed-${DEPLOY_ID}"
  mv "${BETTERKYLE_VENV}.pre-${DEPLOY_ID}" "$BETTERKYLE_VENV"
fi
```

Verify the still-active database, then restart the old BetterKyle version:

```bash
sqlite3 "$BETTERKYLE_DB_SOURCE" "PRAGMA integrity_check;"
sqlite3 "$BETTERKYLE_DB_SOURCE" \
  "SELECT 'users', COUNT(*) FROM users UNION ALL SELECT 'matches', COUNT(*) FROM matches;"
sudo systemctl daemon-reload
sudo systemctl start "$BETTERKYLE_SERVICE"
sudo systemctl status "$BETTERKYLE_SERVICE" --no-pager
sudo journalctl -u "$BETTERKYLE_SERVICE" -n 150 --no-pager
```

The current implementation makes no music/radio database schema changes and
only performs backwards-safe League column additions, so the old application
should normally use the post-deployment `users.db`. This retains links and any
match activity recorded after cutover.

Only if the active database fails integrity checks should the final pre-cutover
backup replace it. Even then, preserve the failed file first:

```bash
mv "$BETTERKYLE_DB_SOURCE" \
  "$BETTERKYLE_BACKUP_DIR/users.corrupt-${DEPLOY_ID}.db"
install -m 600 "$BETTERKYLE_BACKUP_DIR/users.db" "$BETTERKYLE_DB_SOURCE"
sudo chown "${BETTERKYLE_USER}:" "$BETTERKYLE_DB_SOURCE"
sqlite3 "$BETTERKYLE_DB_SOURCE" "PRAGMA integrity_check;"
```

If `.env` was damaged, preserve it and restore the original in the same way:

```bash
mv "$BETTERKYLE_DIR/.env" "$BETTERKYLE_BACKUP_DIR/.env.damaged-${DEPLOY_ID}"
install -m 600 "$BETTERKYLE_BACKUP_DIR/.env" "$BETTERKYLE_DIR/.env"
sudo chown "${BETTERKYLE_USER}:" "$BETTERKYLE_DIR/.env"
```

After the old bot is confirmed healthy, Lavalink may remain stopped. Disable it
if the rollback is expected to last; this does not delete the service, JAR,
configuration, logs, or plugins:

```bash
sudo systemctl disable lavalink.service
```

Keep all backup files, the failed deployment environment, the deployed commit
hash, and the old commit hash until the incident is fully resolved.
