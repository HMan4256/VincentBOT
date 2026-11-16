# Development and rollout checklist

The bot has been used on Discord; behavior, permissions and provider limits still need validation in your own server. Connecting it enables messages and reactions.

## Before connecting

1. Choose a test server/channel and bot account. Enable Message Content intent in the Discord Developer Portal. The Server Members intent is needed only if `WELCOME_CHANNEL_ID` enables welcome messages; enable it in the portal in that case. Grant View Channel, Read Message History, Send Messages and Add Reactions. Grant Manage Messages only if your test specifically needs it.
2. Create a private `.env.v2.dev` file (ignored by Git). Configure the test bot token, an OpenRouter key, model, test channel ID and owner ID. Suggested starting settings:

   ```dotenv
   DISCORD_TOKEN=your-test-bot-token
   OPENROUTER_API_KEY=your-key
   OPENROUTER_MODEL=your-model-id
   CHANNEL_IDS=your-test-channel-id
   VINCENT_OWNER_ID=your-user-id
   VINCENT_DATABASE=data/vincent-v2-dev.sqlite3
   VINCENT_UNSOLICITED_ENABLED=false
   VINCENT_AUTO_PROMOTE=false
   VINCENT_MAX_DAILY=1000
   ```

   Add `WELCOME_CHANNEL_ID` only if it is in `CHANNEL_IDS`. Configure quota reset timezone/hour based on your provider. The local cap is per database; if the key is also used elsewhere, the provider quota snapshot may be the limiting factor. Monitor actual usage.
3. Run `.venv/bin/python -m unittest discover -s tests -v` and `.venv/bin/python -m vincent.preflight --dotenv .env.v2.dev`. Inspect warnings. The offline preflight does not verify Discord permissions, token validity, actual provider quota semantics, or whether another bot process is already running.

## Development test

Start only when ready: `VINCENT_ENV_FILE=.env.v2.dev .venv/bin/python -m vincent.app`. Do not paste keys into logs. Exercise simultaneous direct users, implicit follow-ups, unrelated overlapping replies, rate-limit handling, edit/delete, two reaction choices and removals, memory list/correct/forget/off, restart and uncertain send recovery. Check logs and `!vin admin status`, then inspect the database and report tool for traceability. Verify that no attachment bytes/files were downloaded and that private profile information is not exposed in another person's response. Use test data only.

Start unsolicited participation only after direct routing is stable. Test it in the private channel with low limits and ensure stale drafts are suppressed. Test prompt proposal collection separately; don't promote a proposal solely on automatic votes. Canaries and optional auto-promotion should remain off until offline review and the test feedback path are verified. The current bot's own messages and media are not guaranteed erasable from Discord or external provider copies by local deletion.

## Production use

Back up the bot database before making changes, and confirm the channel IDs, permissions and quota settings. Stop any other process using the same bot account before starting Vincent. Watch the queue, model errors, budget, reactions and responses during the first run.

## Uncertain sends

A send can be accepted by Discord while the process loses the response. The bot records a nonce before sending and tries to recover a matching message from recent history. Discord history may not expose the nonce; such items remain uncertain and are never automatically resent. Inspect them locally with `.venv/bin/python -m vincent.manage --database PATH uncertain-list`. After independently checking the channel, `resolve-sent QUEUE_ID MESSAGE_ID` records a verified Discord message, while `discard-uncertain QUEUE_ID` cancels the item without resending. Manual resolution does not retrieve missing message content; diagnostics may be incomplete for that item.
