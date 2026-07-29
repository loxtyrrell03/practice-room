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

## Phone / anywhere — no login

Open **https://lox.tail89d19b.ts.net:10000/** on the phone. Bookmark it or add it
to the Home Screen. There is no Practice Room login, token, QR code, or pairing
step: the phone reaches the laptop over the private Tailscale network already
used by both devices. The GitHub credential never enters the browser.

The laptop server starts automatically when Lox signs in to Windows, and
Tailscale Serve keeps the HTTPS address across restarts. The laptop must be
awake and Tailscale must be connected on both devices. The public GitHub Pages
address now forwards to the private address so old bookmarks do not show setup.

This public repo contains only the app shell and server — no personal data.
