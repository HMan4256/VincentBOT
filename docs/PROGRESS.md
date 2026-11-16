# Project status

Vincent has been used in a live Discord server. The project remains an evolving personal bot, not a production-ready hosted service. Tests cover individual components, synthetic model responses and selected integration paths; they do not establish humor quality, memory accuracy or reliability in every server.

## Current capabilities

- Discord routing, follow-up handling, per-branch queues and limited direct-request batching.
- Persistent shared and user-scoped memories with source checks, correction and deletion. A local multilingual embedding model ranks relevant memories; lexical matching is the fallback if the model is unavailable.
- Batched memory extraction, request quotas and a local dashboard for operation and inspection.
- Reaction collection and guidance proposals, with reviewed canary support. Automatic promotion is off by default.

## Known limits

- Semantic retrieval and extraction are imperfect; a relevant fact may be missed or a memory may be wrong. Inferred relationship records are not established truth.
- Background processing competes for a finite request budget. A full memory backlog can take multiple days if the configured daily allocation is exhausted.
- Offline periods are not automatically backfilled from Discord history. A bounded queue or request exhaustion can also cause work to expire.
- A local data deletion cannot retract messages already sent to Discord, provider-side copies, or independent backups.
- The dashboard is intended for local use. It has not been designed as an internet-facing multi-user application.
- Most tests use synthetic inputs and mocked Discord/provider responses. Real-server precision, recall and humor quality have not been independently measured.

See [deployment notes](DEPLOYMENT.md) and [evaluation](../evaluation/README.md) for practical checks.
