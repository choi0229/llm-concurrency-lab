# Unit 5 — Common Metric Parity Audit

Status: Unit 5 functional cross-validation
Date: 2026-08-22

Names confirmed from actual `/actuator/prometheus` output captured during the F1–F5 harness runs
(`docs/test-results/phase3/unit5-cross-validation/{A,B,C}/*/prometheus-after.txt`), not assumed.

## Common metrics — name + semantic parity confirmed

All three implementations expose these under the identical Prometheus name, with the identical
`outcome`-tag semantics for `gateway_requests_total` (Unit 1's frozen 7-value set):

```
gateway_requests_started_total
gateway_requests_total{outcome="..."}
gateway_active_streams
gateway_admission_active
gateway_admission_rejected_total
gateway_upstream_active
gateway_upstream_cancel_total          (lazily registered on first non-"completed"-non-"rejected"
                                         terminal — same semantic in all three as of the Unit 5.5
                                         §0 correction; absent from F1/F3 (completed/rejected only,
                                         no upstream ever cancelled), present in F2/F4/F5)
gateway_client_disconnect_total
gateway_timeout_total
gateway_bytes_relayed_total            (parity caveat — see phase3-metrics-contract.md §8-2)
gateway_first_chunk_relay_seconds_{count,sum,max}   (parity caveat — see §8-1 below)
gateway_stream_duration_seconds_{count,sum,max}     (parity caveat — see §8-1 below)
```

**Values are not required to be equal** — only the name and label semantics. Two of these
(`bytes_relayed`, `first_chunk_relay`/`stream_duration`) have confirmed physical-boundary
differences documented in `docs/decisions/phase3-metrics-contract.md` §8 — frozen there as
implementation diagnostics, not cross-implementation performance metrics. Not repeated in full
here; that ADR section is the authoritative record.

## Implementation-specific metric matrix

| | P3-A | P3-B | P3-C |
|---|---|---|---|
| `gateway_watchdog_activated_total` | present | present | **absent** (no AsyncContext-equivalent safety watchdog concept — Unit 4 §14) |
| `gateway_write_overflow_total` | present | present | **absent** (no write buffer to overflow — Unit 4 §10) |
| `outbound_blocking_executor_*` | present (active/pool_size/largest_pool_size/queue_depth/rejected, + `outbound_blocking_task_duration_seconds`) | **absent** (no blocking outbound executor) | **absent** |
| `servlet_write_executor_*` / `servlet_write_stream_buffered_frames` / `servlet_write_duration_seconds` / `servlet_write_queue_wait_seconds` | present | present | **absent** (no Servlet write path at all) |
| `tomcat_*` | present (Tomcat 9.0.83, confirmed Unit 1/2 spike) | present | **absent** (no Tomcat) |
| `reactor_netty_connection_provider_*` / `reactor_netty_http_client_*` | **absent** (no WebClient) | present | present |
| `reactor_netty_http_server_*` | absent (no Reactor Netty at all) | absent (server is Tomcat) | **absent even here** (Unit 4 finding: Boot's default embedded server doesn't enable Reactor Netty server metrics on its own) |
| `http_server_requests_seconds_*` | absent (this is WebFlux-specific Micrometer auto-instrumentation) | absent | present |

No implementation has a metric faked as an always-zero placeholder for a resource it doesn't have
— confirmed by grep against the full snapshots (`grep -E "^tomcat_|^servlet_|^outbound_blocking"`
returns nothing for P3-C; `grep "reactor_netty_http_client"` returns nothing for P3-A).

## Verified invariants (Unit 1 ADR §26, re-confirmed here per scenario)

All five scenarios, all three implementations:

```
gateway_requests_started_total(after) - gateway_requests_started_total(before)
  == sum(gateway_requests_total{outcome=*} delta)
```

Holds exactly in every one of the 15 (config × scenario) runs — see each scenario's
`accounting.txt`. Postflight gauges (`gateway_active_streams`, `gateway_admission_active`,
`gateway_upstream_active`, plus whichever implementation-specific ones apply) are 0 in every run.
