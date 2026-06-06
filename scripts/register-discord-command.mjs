// One-time: registers the /analyse slash command with Discord.
//
// Run locally (NOT in Vercel). PowerShell:
//   $env:DISCORD_APP_ID="..."; $env:DISCORD_BOT_TOKEN="..."; node scripts/register-discord-command.mjs
// bash:
//   DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... node scripts/register-discord-command.mjs
//
// Optionally set DISCORD_GUILD_ID to register to ONE server for INSTANT testing
// (global commands can take up to ~1 hour to appear). Omit it for the global command.

const APP_ID = process.env.DISCORD_APP_ID;
const TOKEN  = process.env.DISCORD_BOT_TOKEN;
const GUILD  = process.env.DISCORD_GUILD_ID;

if (!APP_ID || !TOKEN) {
  console.error('Set DISCORD_APP_ID and DISCORD_BOT_TOKEN (and optionally DISCORD_GUILD_ID).');
  process.exit(1);
}

const command = {
  name: 'analyse',
  description: "APEX's latest published verdict for a ticker (BUY/SELL/WAIT + levels)",
  options: [
    { name: 'ticker', description: 'e.g. BTC, NVDA, EUR/USD', type: 3, required: true }, // type 3 = STRING
  ],
};

const url = GUILD
  ? `https://discord.com/api/v10/applications/${APP_ID}/guilds/${GUILD}/commands`
  : `https://discord.com/api/v10/applications/${APP_ID}/commands`;

const res = await fetch(url, {
  method: 'POST',
  headers: { Authorization: `Bot ${TOKEN}`, 'Content-Type': 'application/json' },
  body: JSON.stringify(command),
});

const text = await res.text();
if (res.ok) {
  console.log(`OK — /analyse registered ${GUILD ? `to guild ${GUILD} (instant)` : 'globally (may take up to ~1h)'}.`);
} else {
  console.error(`FAILED (HTTP ${res.status}):`, text);
  process.exit(1);
}
