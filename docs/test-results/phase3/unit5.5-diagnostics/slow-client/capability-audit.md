# Unit 5.5 D2 — xk6-sse Slow-Client Capability Audit

Status: Unit 5.5 §8-10
Date: 2026-08-22

## Question

Can `k6 v1.8.0` + `xk6-sse v0.1.11` (the exact pinned combination this repo already uses,
`load-test-k6/Dockerfile`) implement a client that genuinely slows the **TCP-level** read of an
SSE response body — not just one that processes received events slowly at the JS layer while the
underlying socket keeps draining at full speed?

## Method

Read the actual `xk6-sse@v0.1.11` Go source directly (already present in this machine's local Go
module cache — `~/…/.native-runtime/go/pkg/mod/github.com/phymbert/xk6-sse@v0.1.11/sse.go` — not
guessed from documentation) rather than assuming behavior from the JS-facing API alone.

## Finding: yes — confirmed by the channel design, not by documentation claims

`sse.go`'s `Open()` sets up:

```go
readEventChan := make(chan Event)   // UNBUFFERED
go client.readEvents(readEventChan, readErrChan, readCloseChan)

for {
    select {
    case event := <-readEventChan:
        ...
        client.handleEvent("event", rt.ToValue(event))   // runs the JS callback SYNCHRONOUSLY
    ...
    }
}
```

and the reader goroutine (`readEvents`, running concurrently, doing the actual
`bufio.NewReader(c.resp.Body)` / `reader.ReadBytes('\n')` socket reads):

```go
select {
case readChan <- ev:     // BLOCKS here until the control loop above receives it
    buf.Reset()
    ev = Event{}
case <-c.done:
    return
}
```

Because `readEventChan` is **unbuffered**, `readChan <- ev` cannot complete until the single
control-loop goroutine is back at its `select` — which only happens after
`client.handleEvent("event", ...)` (the JS callback) **returns**. If the JS callback calls `sleep()`,
the reader goroutine is blocked on that channel send for the whole sleep duration, and therefore
never calls `reader.ReadBytes('\n')` again during that window — the Go HTTP client's response body
`Read()` is not invoked, so the OS-level TCP receive buffer is not drained, so real TCP flow
control (shrinking receive window) applies exactly as it would for any genuinely slow reader.

**This is not the same thing as "the callback is slow but the socket read races ahead of it"** —
confirmed by design, not assumed: there is no buffering between the socket reader and the JS
callback beyond a single unbuffered handoff.

## Additional finding during first live run (tool-usage bug, not a Gateway bug)

The first P3-B diagnostic run hung for 50+ seconds after the Gateway aborted the connection
(`write_overflow`, see `SUMMARY.md`). Root cause, confirmed by re-reading `sse.go`'s control loop:
when the reader goroutine hits a socket error (e.g. the peer closing the connection) it sends to
`errorChan` and **returns** — but the control loop's `case readErr := <-readErrChan:` branch only
invokes the JS `error` handler and loops back to `select`, it does not itself close/return. If the
script's `error` handler doesn't explicitly call `client.close()`, the control loop waits forever on
a `select` that nothing will ever satisfy again (the reader goroutine is already gone). Fixed in
`scripts/diagnostic-tools/slow-client-k6.js`'s `error` handler by calling `client.close()` — a
diagnostic-script fix, not a change to any Gateway.

## Decision

xk6-sse v0.1.11 is used directly for Unit 5.5 D2 (`scripts/diagnostic-tools/slow-client-k6.js`) —
no separate custom slow-client tool was built. Sleeping inside the `client.on('event', ...)`
callback (via k6's `sleep()`) is the mechanism; workload sizing (large payload, fast production) is
handled on the Mock LLM side — see `SUMMARY.md` for the exact parameters used and why.
