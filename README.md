# AIGC daily cloud runtime

GitHub schedules this program from 02:07 UTC (10:07 Beijing time). It fetches AIHOT candidates, passes the editorial contract and seven days of previously sent news to the configured model, validates its JSON, then sends a Feishu card through the official Lark CLI. It needs no personal computer or interactive agent session. GitHub schedules can be delayed or skipped; successful manual runs do not prove schedule delivery.

Recovery checks run at :07, :27 and :47 until 09:47 UTC. Completed days return before model calls. At most six model calls per publication day are allowed; manual previews have a separate six-call daily cap. This uses existing model API billing, not a bundled GitHub model allowance.

The `validate` dispatch mode makes a real model call, validates the resulting card, checks bot identity, runs the CLI send dry-run, and reads an existing migrated message. It sends nothing and does not freeze or mark the production day complete. `scheduled` obeys the 10:00–18:00 Beijing delivery window and daily deduplication.

`state.enc` is an authenticated encrypted checkpoint. It contains private history, editorial decisions, sending intent and receipts. Missing state, failed persistence, wrong bot/chat, unknown delivery outcome or invalid editorial output stop the run. Pending intent is persisted before sending, the receipt is persisted before readback, and readback failure never causes another send. Uncertain sends require manual reconciliation. State is not silently reset or trimmed.

Configuration:

Candidate collection combines the official daily, rolling selected items, and seven explicit creative-topic searches of AIHOT's public pool (video, image, audio, music, speech, digital humans and short drama). Non-selected results retain their status and provenance; search matches are candidates, not recommendations. All query pages must complete, with a five-page-per-query and 70-unique-candidate guard that fails rather than silently truncates. This scope is for internal creative reporting, not a public API mirror.

- Secrets: `STATE_KEY`, `LARK_APP_ID`, `LARK_APP_SECRET`, `LARK_CHAT_ID`, `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`.
- Variables: `DEPLOYMENT_REPOSITORY` must equal this repository; `CLOUD_DAILY_ENABLED=true` enables scheduled runs after migration validation. Forks and non-main dispatches cannot run the delivery job.
- `state.enc` must be explicitly initialized from verified prior receipts before activation.
- Standard GitHub-hosted public-repository runners are used. No plaintext news artifacts, private message identifiers, provider responses or credentials are printed in public logs.
- Only `schedule` and `workflow_dispatch` start the job; news content and model output cannot execute commands, select recipients, or change scheduling.

This is a deployment snapshot of the Daily skill runtime. `source-manifest.json` records source and exported hashes. The exporter substitutes local bot/chat/path defaults with required environment configuration and includes only the required files. Update the canonical skill source and re-export; do not maintain a divergent cloud prompt. Source snapshots do not include local credentials or unrelated skills. This runtime does not include the separate personal full-news daily or hotspot alerts.
