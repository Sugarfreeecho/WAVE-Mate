# Runtime V2 sidecar invariants

- Main sessions keep exactly one append-only `events.jsonl`; do not introduce segmented logs without measured need.
- Subagent details are stored under the parent session at `subagents/{agent_id}/events.jsonl`, with independent `snapshots/latest.json` and optional `metadata.json`.
- Large text payloads are externalized to `blobs/{sha256}.txt`; events keep only the blob reference.
- Normal append paths update snapshots incrementally from the previous snapshot plus the new event. Full replay is a fallback for repair or missing snapshots.
- On-demand reads are provided by `read_latest(limit)`, `read_before_seq(before_seq, limit)`, and `read_after_seq(after_seq)` so UI/debug tools do not need to load full logs.
- Legacy destructive paths do not rewrite Runtime V2 message events. They append observation/semantic events such as `legacy_truncate_observed`, `legacy_branch_observed`, `legacy_tail_restored_observed`, and subagent delete observations.
- `messages` is the raw projected message stream; `visible_messages` applies delete/rewrite/visible-range semantics; `model_messages` applies model-window and compaction semantics.
