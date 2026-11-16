# Offline Vincent evaluation

This is a first experimental harness, separate from the old bot. It neither connects to Discord nor changes the bot. The 16 fictional scenarios are development cases, not a blind holdout or a verified measure of humor. `persona.txt` is a starting hypothesis, not the final personality prompt. Fixture responses only exercise the software and are not examples judged good by HMAN.

## Run without a model

```bash
python evaluation/runner.py run --backend fixture --output /tmp/vin-fixture-a.json
python evaluation/runner.py run --backend fixture --output /tmp/vin-fixture-b.json
python evaluation/runner.py compare --run-a /tmp/vin-fixture-a.json --run-b /tmp/vin-fixture-b.json --output /tmp/vin-blind.json --key /tmp/vin-key.json
python -m unittest discover -s tests -v
```

Output files must not already exist. The two fixture runs are identical, so their comparison verifies the workflow, not quality.

## Run a model when ready

Set `OPENROUTER_API_KEY` in the shell environment, then explicitly choose `--backend openrouter --model MODEL_ID` and an output file. The script does not load `.env`. Each scenario makes one request to OpenRouter, incurs the provider's applicable costs and uses the specified model; only fictional scenario context and the persona prompt are sent. No request happens in fixture mode. For example:

```bash
python evaluation/runner.py run --backend openrouter --model YOUR_MODEL_ID --output /tmp/vin-model-a.json
```

An input allowlist sends only mode, message author, message text and optional reply author; review focus, labels, expected responses and future follow-ups are excluded. Run files store model and generation settings, hashes of the prompt and scenario files, and outputs. They never include the API key. A provider failure stops the run rather than silently treating a partial run as complete. This initial runner makes sequential requests and does not retry, resume or enforce a provider quota; use small controlled sets when comparing paid models.

## Blind review

Compare two complete runs with `compare`. The review JSON anonymizes run names, randomizes case order, and balances left/right assignments. The separate key identifies the source of each answer; keep it hidden until ratings are complete. The review focus describes what the reviewer should look for; it is never sent to the model. The rating fields support a preferred response, response appropriateness, separate 1–5 ratings for understanding, voice, timing, creativity, entertainment and wildness, flags, and notes. For silence, rate appropriateness and leave response-only dimensions empty. This initial version records ratings in editable JSON; it does not yet validate or summarize ratings.

Scenarios are synthetic development cases and have been reviewed during creation. Before claiming blind holdout results, separate conversations by thread and date, and check for near-duplicates across sets. Reviewing real archive content requires separate data handling; this runner currently uses synthetic cases only.

The optional archive replay scripts default to the synthetic bot ID `9`. Set `VINCENT_REPLAY_BOT_ID` to the bot ID used in your private export before replaying it. Exports and replay output can contain sensitive information and should stay outside the public repository.
