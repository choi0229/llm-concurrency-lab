# Mock pacing algorithm — exact source audit

Source: `mock-llm-fastapi/app/main.py` (current, unmodified since before Unit 5 — confirmed by
mtime diff=0 checks throughout Units 5/6). Read directly, not from memory/design docs.

## `StreamRequest` model (lines 47-65)

```python
firstChunkDelayMs: int = Field(default=200, ge=0)
chunkIntervalMs: int = Field(default=100, ge=0)
chunkCount: int = Field(default=10, ge=1)
```

Defaults (200/100/10) are NOT the frozen Phase 4 workload values (1000/200/35) — if a field were
ever missing from the request body, Pydantic would silently substitute these defaults, which
would show up as visibly different `chunkCount` (event-count mismatch) and `firstChunkDelayMs`
(logged directly). Neither was observed (see event-count-audit.md).

## `generate_stream()` pacing (lines 72-190)

```python
start = time.monotonic()                          # line 74 -- MONOTONIC clock
...
await asyncio.sleep(req.firstChunkDelayMs / 1000)  # line 111 -- ONE initial delay
first_chunk_latency.observe(time.monotonic() - start)  # line 112
...
for sequence in range(1, req.chunkCount + 1):      # line 120
    ...
    yield sse_event("delta", data)                 # line 141
    ...
    if sequence < req.chunkCount:                  # line 150
        ...
        await asyncio.sleep(req.chunkIntervalMs / 1000)  # line 155
...
yield sse_event("final", '{"status":"COMPLETED"}') # line 157, no further sleep
...
finally:
    stream_duration.observe(time.monotonic() - start)  # line 179 -- MONOTONIC clock
```

**Exact sleep count**: 1 (firstChunkDelay) + `chunkCount - 1` (chunkInterval, since the loop
guards `sequence < chunkCount` -- sequence `chunkCount` itself does not sleep after its `delta`).
For chunkCount=35: 1 + 34 = 35 sleep calls total.

**Theoretical minimum elapsed time** (from the current source, not a design document):
`firstChunkDelayMs + (chunkCount - 1) * chunkIntervalMs = 1000 + 34*200 = 7800`ms.

**Event count per completed request**: `chunkCount` delta events + 1 final event = 35 + 1 = **36**,
confirmed against raw `sse_event` counts (event-count-audit.md).

## Can `asyncio.sleep(x)` return before `x` seconds elapse?

No. `asyncio.sleep()` is implemented via the event loop's `call_later`, which schedules a callback
no earlier than the requested delay after being scheduled — CPython's asyncio (and uvloop, which
this Mock does not appear to explicitly install, so it runs on the standard library's default
`SelectorEventLoop`/`ProactorEventLoop` equivalent for the platform) provides no mechanism for a
timer to fire *early*. Event-loop contention under load can only *delay* a scheduled callback
(scheduler is busy running other coroutines when the timer fires), never make it fire ahead of
schedule. **There is no code path in this file, and no documented asyncio semantic, under which
340 accumulated `asyncio.sleep(0.2)` calls could sum to less than 6.8s of real elapsed monotonic
time.** This rules out "Case B" (a Mock-semantic explanation for genuinely faster completion).

## Clock sources used

- `start = time.monotonic()` and `stream_duration.observe(time.monotonic() - start)`: **monotonic**
  clock — immune to wall-clock adjustments (NTP step, system clock sync), can never run backward.
- `log(request_id, message)` -> `logger.info(...)`: Python's standard `logging` module, whose
  default `Formatter` timestamps records using `time.time()` (**wall clock**) unless a custom
  `converter`/`Formatter` is configured. No custom formatter is configured in this file (no
  `logging.Formatter`/`basicConfig` override visible in `main.py` beyond the default). So the
  human-readable `mock.log` "stream requested"/"completed" lines are wall-clock timestamps, while
  the `mockllm_stream_duration_seconds` / `mockllm_first_chunk_latency_seconds` Prometheus
  histograms are monotonic-clock-based. These are two independently-clocked measurements of the
  same request lifecycle, which is exactly what makes the cross-check in `clock-audit.md`
  possible.
