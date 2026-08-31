# Workload provenance — Canary / F6-rep1 / F6-rep2 / F6-rep3 (M3 N=640)

All four values below are read from each run's own `environment.json` (written by the frozen
harness `scripts/run-phase4-closed-benchmark.sh` from its own hardcoded shell variables --
`FIRST_CHUNK_DELAY_MS=1000`, `CHUNK_INTERVAL_MS=200`, `CHUNK_COUNT=35`, lines 28-30 of that
script, unconditional and identical for every invocation) and independently cross-checked against
Mock's own request log and internal metrics -- not assumed from documentation.

| Run | firstChunkDelayMs (env) | chunkIntervalMs (env) | chunkCount (env) | Mock log firstChunkDelayMs | Mock internal avg first-chunk latency (monotonic) | Event count (35 delta + 1 final = 36) |
|---|---|---|---|---|---|---|
| Canary | 1000 | 200 | 35 | 1000 | 1.0007s | 36 (23220/645 prewarm+wave... see event-count-audit.md) |
| F6-rep1 | 1000 | 200 | 35 | 1000 | 1.0008s | 36 |
| F6-rep2 | 1000 | 200 | 35 | 1000 | 1.0009s | 36 |
| F6-rep3 | 1000 | 200 | 35 | 1000 | 1.0011s | 36 |

**No difference in any recorded/derivable workload parameter across the four runs.** The
`environment.json` values are identical (expected, since the harness hardcodes them
unconditionally, not read from a variable that could plausibly differ run-to-run). More
importantly, the two *independent, code-level* signals that do NOT merely restate the
`environment.json` value — Mock's own logged `firstChunkDelayMs` and Mock's internal
monotonic-clock `first_chunk_latency` histogram average — both confirm ~1.000-1.001s for every one
of the four runs, including F6-rep3. This directly answers the "150ms chunkInterval" hypothesis:
if `chunkIntervalMs` had actually been altered for F6-rep3 while `firstChunkDelayMs` stayed at
1000, that would be a request-body-specific change that a byte-for-byte-passthrough Gateway
(confirmed below) could not itself introduce, and it still would not explain compression on the
`firstChunkDelayMs` leg specifically -- but F6-rep3's own first-chunk latency (1.0011s) matches
its siblings, so there is no evidence of an *altered request parameter* for this run.

## Request path: Gateway is a byte-for-byte passthrough (rules out Gateway-side alteration)

`gateway-phase4-webflux/.../ChatHandler.java`:

```java
return request.bodyToMono(String.class)
        .defaultIfEmpty("{}")
        .flatMap(body -> handle(requestId, body));
...
webClient.post().uri("/mock/stream")...bodyValue(requestBody)...
```

The Gateway reads the incoming request body as an opaque `String` and forwards it verbatim to
Mock via `bodyValue(requestBody)` -- it never deserializes or touches `chunkIntervalMs`/
`firstChunkDelayMs`/`chunkCount` (grep for these names across
`gateway-phase4-webflux/src/main` returns zero matches). The k6 script
(`load-test-k6/scenarios/04-phase4-closed-wave.js`) builds the same fixed JSON body
(`firstChunkDelayMs`/`chunkIntervalMs`/`chunkCount`/`chunkSizeBytes` from its own `__ENV` values,
themselves fixed by the harness's hardcoded shell variables) for every VU/iteration. There is no
mechanism in this request path for one specific run's request body to differ from another's while
the harness script and k6 scenario file remain byte-identical (confirmed unchanged throughout
Units 5/6 by SHA256).

## Conclusion (this document only)

No evidence of an actual workload/config parameter difference for F6-rep3. Case A
(actual workload/config drift) is not supported by any provenance evidence gathered.
