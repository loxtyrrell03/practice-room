# Practice Room

Twenty-eight days to the recital. Today's practice blocks with built-in timers,
piece security meters, a journal, and a coach (Claude, via the Claude CLI) that
knows the whole plan, remembers what you tell it, and rebuilds tomorrow around
how today actually went.

## Use it (zero setup)

Double-click **`Practice Room.bat`** in your local folder
(`C:\Users\loxty\Desktop\Repos\practice-room`). That's it — the site opens at
`http://localhost:8977`, data reads/writes go straight to disk, and the coach
runs through your already-logged-in Claude CLI. No tokens, no accounts.
Everything syncs to the private `practice-room-data` repo in the background as
a backup (best effort — offline is fine).

To set the folder up on another machine:

```bash
git clone https://github.com/loxtyrrell03/practice-room
git clone https://github.com/loxtyrrell03/practice-room-data practice-room/data-repo
```

## Phone / anywhere

With the local server running, open **http://localhost:8977/pair** on the PC and
scan the QR with your phone — it opens the hosted site
(https://loxtyrrell03.github.io/practice-room/) already signed in. From then on
the phone works anywhere with wifi: it reads/writes the private data repo
directly, and messages you send are answered by your PC's coach within ~a minute
whenever the PC is on.

Want coach replies even with the PC off? One-time, on the PC:
`claude setup-token`, then
`gh secret set CLAUDE_CODE_OAUTH_TOKEN -R loxtyrrell03/practice-room-data` —
GitHub Actions then answers whenever the local server isn't around to.

This public repo contains only the app shell and server — no personal data.
