# xk6-sse hang bug repro (diagnostic-tool bug, not a Gateway bug)

`k6-output.txt` is the raw output from the very first live slow-client run against P3-B, before
the `error` handler in `scripts/diagnostic-tools/slow-client-k6.js` called `client.close()`. The
server aborted the connection (overflow → `client_disconnect`/reset), the k6 script's `error`
handler ran but never closed the client, and xk6-sse's control loop then had nothing left to
receive on any channel — k6 hung with `0 complete and 0 interrupted iterations` indefinitely (this
capture was manually terminated at ~59s).

Root cause and fix: `docs/test-results/phase3/unit5.5-diagnostics/slow-client/capability-audit.md`
("Additional finding during first live run"). Kept here only as the raw evidence for that finding
— not a scenario result, do not compare against `overflow-scenario/` or `sustained-scenario/`.
