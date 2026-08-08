# Manual production actions

These are the actions that require access to accounts or to the Ubuntu server.
All local code and source-controlled configuration, including the live-radio
station URLs, are already handled in this repository.

- [ ] Review `DEPLOYMENT.md`, schedule a maintenance window, and identify the
  real production checkout path, service user, BetterKyle unit name, and virtual
  environment path.
- [ ] Publish or securely transfer the exact tested BetterKyle commit so the
  Ubuntu checkout can fetch it, and record that commit hash.
- [ ] Create Spotify application credentials in the Spotify Developer Dashboard.
- [ ] On Ubuntu, preserve the existing `.env` values and add a unique
  `LAVALINK_PASSWORD`, `LAVALINK_URI`, `SPOTIFY_CLIENT_ID`, and
  `SPOTIFY_CLIENT_SECRET` as documented.
- [ ] Take and verify the code, Git, systemd, `.env`, and SQLite backups before
  stopping the existing bot.
- [ ] Install Python 3.12, build the production venv from `requirements.txt`, and
  install OpenJDK 17 or newer on Ubuntu.
- [ ] Download the pinned Lavalink 4.2.2 JAR, install the provided systemd service
  example with the real user/paths, and verify LavaSrc 4.8.3 loads.
- [ ] Confirm the Discord bot role has Connect, Speak, and Use Application
  Commands permissions in the intended channels.
- [ ] Perform the documented cutover and complete the League, music, Spotify,
  SoundCloud, queue, and live-radio production verification checklist.
- [ ] Retain the protected database, `.env`, old commit, old venv, and deployment
  backup until the new version has been stable for an agreed retention period.

There is no manual radio URL task. Public stream URLs are source-controlled in
`music/radio_stations.py`, not `.env`.
