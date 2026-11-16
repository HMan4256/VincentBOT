# Vincent cheatsheet

Run terminal commands from the project folder. `Ctrl+C` stops the foreground process.

## Run & check

```bash
.venv/bin/python -m vincent.dashboard_server       # Dashboard: http://127.0.0.1:8765
.venv/bin/python -m vincent.app                    # Start the bot directly
.venv/bin/python -m vincent.preflight               # Check configuration offline
.venv/bin/python -m vincent.semantic_memory         # Download the local memory model once
.venv/bin/python -m unittest discover -s tests -q  # Run tests
.venv/bin/python -m vincent.report --days 1        # Aggregate report
.venv/bin/python -m vincent.memory_now             # Process the current memory backlog now
.venv/bin/python -m vincent.manage --help          # All local management options
```

The dashboard can start and stop a bot it launched. Closing the dashboard also stops that bot. Don't run two copies of Vincent on the same account. To use another env file for the bot or dashboard, prefix its command with `VINCENT_ENV_FILE=.env.v2.dev`. For preflight, use `--dotenv .env.v2.dev` instead.

## Discord: everyone

```text
!vin help                     Show commands
!vin quota                    Show request usage
!vin memory list              DM your saved memories
!vin memory add TEXT          Save a fact about yourself
!vin memory correct ID TEXT   Correct one of your memories
!vin memory delete ID         Delete one of your memories
!vin memory forget            Delete your profile memories
!vin memory off               Disable memory collection and use
!vin memory on                Enable memory collection and use
!vin memory auto off          Disable automatic extraction
!vin memory auto on           Enable automatic extraction
!vin data forget              Erase your local data and disable memory
```

You can also mention Vincent, reply to him, or use his name. Memory lists arrive by DM.

## Discord: server managers

Replace `on|off` with either `on` or `off`. Commands apply to the current channel where relevant.

```text
!vin admin status                      Budget, queue and guidance status
!vin admin feature unsolicited on|off  Toggle unprompted messages
!vin admin feature reactions on|off    Toggle rating reactions
!vin admin context clear               Clear this channel's short-term context
!vin admin memories                    List shared memories
!vin admin remember server TEXT        Add a server memory
!vin admin memory delete ID            Delete a shared memory
!vin admin proposals                   List guidance proposals
!vin admin remember global TEXT        Add a global memory (owner only)
!vin admin activate ID                 Activate guidance (owner only)
```

Deleting a global memory also requires the configured owner. Set `VINCENT_OWNER_ID` in `.env` for owner-only controls.

## Local guidance & recovery

```bash
.venv/bin/python -m vincent.manage status
.venv/bin/python -m vincent.manage activate VERSION_ID
.venv/bin/python -m vincent.manage core-only
.venv/bin/python -m vincent.manage canary-start VERSION_ID --review REVIEW.json --percent 10
.venv/bin/python -m vincent.manage canary-results EXPERIMENT_ID
.venv/bin/python -m vincent.manage canary-finish EXPERIMENT_ID            # Reject
.venv/bin/python -m vincent.manage canary-finish EXPERIMENT_ID --promote  # Promote if checks pass
.venv/bin/python -m vincent.manage uncertain-list
.venv/bin/python -m vincent.manage resolve-sent QUEUE_ID MESSAGE_ID
.venv/bin/python -m vincent.manage discard-uncertain QUEUE_ID
```

For a non-default database, put `--database PATH` **before** the management command. Only resolve an uncertain send after checking Discord. More details: [README](README.md) and [deployment notes](docs/DEPLOYMENT.md).
