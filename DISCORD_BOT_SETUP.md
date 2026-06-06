# APEX Discord bot — 5-minute setup

The bot adds three slash commands to any Discord server:
- **`/analyse <ticker>`** — APEX's most recent published verdict (BUY/SELL/WAIT +
  confidence + entry/stop/target) with links to the full analysis + track record.
- **`/track-record`** — live win-rate, BUY/SELL accuracy & Brier across all resolved
  calls (wins *and* losses).
- **`/help`** — what APEX is + the command list.
It reads cached verdicts from Supabase — it does **not** run a fresh AI committee, so
it's instant, free, and can't be abused to burn your AI quota.

The endpoint is already deployed at `https://apexfx.vercel.app/api/discord`. You just
need to create a Discord app and point it there.

## Steps

1. **Create the app.** Go to <https://discord.com/developers/applications> → **New
   Application** → name it "APEX FX".

2. **Add the public key to Vercel FIRST (before step 3).**
   - In the app's **General Information** tab, copy the **Public Key**.
   - In Vercel → your apexfx project → **Settings → Environment Variables** → add
     `DISCORD_PUBLIC_KEY` = (that key) → **Redeploy** (so the function picks it up).
   - *(Do this before step 3, or Discord's verification ping will fail.)*

3. **Set the Interactions Endpoint URL.**
   - Back in **General Information**, set **Interactions Endpoint URL** to:
     `https://apexfx.vercel.app/api/discord`
   - Click **Save**. Discord sends a signed test ping; it should save with a green
     ✓ (that confirms signature verification works).

4. **Register the `/analyse` command.** Grab two values:
   - **Application ID** (General Information).
   - **Bot token**: **Bot** tab → **Reset Token** → copy it.
   - (Optional, for instant testing) your test server's **Guild ID** (enable
     Developer Mode in Discord → right-click the server → Copy Server ID).
   - Run locally from the project root (PowerShell):
     ```powershell
     $env:DISCORD_APP_ID="<app id>"; $env:DISCORD_BOT_TOKEN="<bot token>"; $env:DISCORD_GUILD_ID="<your test server id>"; node scripts/register-discord-command.mjs
     ```
     Omit `DISCORD_GUILD_ID` to register it globally (can take up to ~1 hour to
     appear; the guild option is instant for testing).

5. **Invite the bot to a server.**
   - **OAuth2 → URL Generator** → scopes: tick **`applications.commands`** (and
     **`bot`** if you also want it listed as a member) → copy the generated URL →
     open it → add it to your server.

6. **Test it.** In any channel of that server, type `/analyse BTC`. You should get an
   embed with APEX's latest BTC verdict + buttons.

## Notes
- **No always-on host or bot token in production** — Discord calls the Vercel
  function directly; the token is only used locally to register the command.
- **Security**: the function rejects any request whose Ed25519 signature doesn't
  match `DISCORD_PUBLIC_KEY`, so only Discord can invoke it.
- If `/analyse FOO` returns "no published call yet", it just means APEX hasn't
  analysed that ticker recently — run it once on the site and it'll appear.
- Every embed carries the "information & education only — not advice" footer.
