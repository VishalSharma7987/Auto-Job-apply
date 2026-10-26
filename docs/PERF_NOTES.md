# Performance notes

## The 30-minute "hang" (GitHub Actions, 2026-10-26)

**Symptom:** the `Run agent` step ran 29m37s, was killed by `timeout-minutes: 30`, and no Telegram report arrived.

**Cause (reproduced locally, not guessed):** not a stuck socket and not a deadlock. Version `dce436a` wrote the daily
statistics once *per job*: every skipped job did `bump_stat` + `add_skip_reason`, each a read-then-write against
Supabase (2-4 sequential PostgREST round trips, about 0.5-1 s each). One run discovers ~7,600 jobs (greenhouse 4,116,
ashby 2,210, lever 821, arbeitnow 300, ...) and about 7,000 of them are skipped by the title / cheap filters, so the
run needed roughly 2 hours of serial DB writes. It was making progress the whole time (the `skipped` counter in
`daily_stats` rose about 1 per second), which is why nothing looked broken, and because the old code had no stage logs,
no time budget and no `finally` report, nothing was printed and nothing was sent when the runner killed it.

**How it was found:** `faulthandler.dump_traceback_later(120, repeat=True)` (now `--debug-hang`) on the committed code.
The first dump, 2 minutes in, showed the only thread in:

```
httpcore ... read()                       <- waiting for the Supabase response
postgrest/_sync/request_builder.py execute
jobagent/db/supabase_repo.py  _stat_row   <- read-modify-write of the daily_stats row
jobagent/db/supabase_repo.py  add_skip_reason
jobagent/pipeline/graph.py    _skip       <- called once per rejected job
jobagent/pipeline/graph.py    dedupe_node
```

Sampling the `skipped` counter 20 s apart confirmed ~1 skip/s. The fixed code finished the same workload in 81 s.

## What fixed it

- `StatsBuffer` (pipeline/runner.py): counters live in memory and are written in one round trip per stage.
- Batched reads (`get_jobs_by_keys`, `list_applications`) instead of two queries per job.
- Time budget (`RUN_TIME_BUDGET_SECONDS`, default 1200 s) checked between items; once used up, no new work starts.
- Hard backstop (`budget.hard_deadline`): budget + 150 s after start, SIGTERM is delivered to the main thread, which
  becomes `RunInterrupted` and still sends the partial report. This covers a single call that blocks.
- The report is sent from `finalize()` in a `finally` in `run_pipeline`, so success, budget, SIGTERM and crash all report.
- Workflow `timeout-minutes: 28` (our 20-minute budget + 150 s backstop fire first), `PYTHONUNBUFFERED=1`.
- First log line (`run started, mode=..., budget=...s`) is printed before any network call.
- Every outbound call has a hard timeout: HTTP <= 20 s (`utils/http.py`), Supabase 20 s, Telegram 20 s, LLM 60 s
  wall-clock (thread guard, because httpx read timeouts reset on keep-alive bytes).

## Finding the next hang

```
python -X faulthandler -m jobagent run --mode full --debug-hang     # or DEBUG_HANG=1
```

Every 120 s the stack of every thread is printed to stderr; the top frame of the thread that is not idle is the blocking call.

## LLM model choice

`minimax/minimax-m3.1-flash:free` was the fastest free model that returned valid match JSON in the 2026-10-22
benchmark. Free models disappear (HTTP 404) and are rate limited upstream (HTTP 429) without notice; the client switches
to `LLM_FALLBACK_MODELS` on 404 and reports the switch in the Telegram summary.
