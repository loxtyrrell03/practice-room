# Practice Room

Twenty-eight days to the recital. Today's practice blocks with built-in timers,
piece security meters, a journal, and a coach (Claude, via the Claude CLI) that
knows the whole plan, remembers what you tell it, and rebuilds tomorrow around
how today actually went.

## Use it (zero setup)

Double-click **`Practice Room.bat`** in your local folder
(`C:\Users\loxty\PracticeRoom`). That's it — the site opens at
`http://localhost:8977`, data reads/writes go straight to disk, and the coach
runs through your already-logged-in Claude CLI. No tokens, no accounts.
Everything syncs to the private `practice-room-data` repo in the background as
a backup (best effort — offline is fine).

To set the folder up on another machine:

```bash
git clone https://github.com/loxtyrrell03/practice-room PracticeRoom
git clone https://github.com/loxtyrrell03/practice-room-data PracticeRoom/data-repo
```

## Optional: hosted mode (phone / other devices)

The same app is served at https://loxtyrrell03.github.io/practice-room/ and can
talk to the data repo through the GitHub API — that path needs a token pasted
once (`gh auth token`), and the coach runs via GitHub Actions (needs the
`CLAUDE_CODE_OAUTH_TOKEN` secret — see the data repo README). Ignore this
entirely if you only use it at the piano.

This public repo contains only the app shell and server — no personal data.
