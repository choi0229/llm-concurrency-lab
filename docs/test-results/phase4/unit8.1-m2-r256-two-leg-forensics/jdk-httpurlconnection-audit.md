# JDK `HttpURLConnection` disconnect / keep-alive audit — Temurin 21.0.11+10

Source read from the exact pinned build:
`/Users/user/Documents/GitHub/llm-concurrency-lab/.native-runtime/jdk-21.0.11+10/Contents/Home/lib/src.zip`
(`java.base/sun/net/www/protocol/http/HttpURLConnection.java`,
`java.base/sun/net/www/http/HttpClient.java`,
`java.base/sun/net/www/http/ChunkedInputStream.java`,
`java.base/sun/net/www/http/KeepAliveStream.java`,
`java.base/sun/net/www/http/KeepAliveCache.java`).

`java -version` of that runtime: `OpenJDK Runtime Environment Temurin-21.0.11+10 (build 21.0.11+10-LTS)`.

## What the application code actually does

`MockLlmClient.openStream()` (byte-identical M1/M2, see `m1-m2-outbound-parity.md`):

- `new URL("http://127.0.0.1:8000/mock/stream").openConnection()` → `sun.net.www.protocol.http.HttpURLConnection`.
- Sets `Content-Type`, `Accept: text/event-stream`, `POST`, `setDoOutput(true)`,
  `setFixedLengthStreamingMode(len)`, **`setUseCaches(false)`**.
- **No `Connection: close` header is ever set.**

`BlockingMockLlmRelay.relay()`:

- reads the response with `new BufferedReader(new InputStreamReader(connection.getInputStream()))`,
- parses SSE line-by-line, and **`break`s out of the read loop as soon as it sees the blank line
  terminating an `event: final` (or `event: error`) block** — i.e. it does *not* call `readLine()`
  again to reach stream EOF,
- `finally { if (connection != null) connection.disconnect(); }` — unconditional, every request.

## Response framing on this workload

The Mock is a Starlette `StreamingResponse` fed by an async generator with **no `Content-Length`**
→ the response is **HTTP/1.1 `Transfer-Encoding: chunked`**. In `HttpClient.parseHTTP()` this selects
`serverInput = new ChunkedInputStream(...)` (HttpClient.java:1008), **not** `KeepAliveStream`
(HttpClient.java:1075, which is the known-content-length path). So the stream object the relay holds
is a `ChunkedInputStream`, and it is `Hurryable`.

The Mock sends `event: final` then the generator returns almost immediately, so Starlette emits the
chunk terminator `0\r\n\r\n` right after. The relay breaks on `final` **before** consuming that
terminator → at `disconnect()` time `ChunkedInputStream.state != STATE_DONE`.

## `connection.disconnect()` — exact path (HttpURLConnection.java:3097)

Because `getInputStream()` succeeded on the success path, `inputStream != null`, so:

```
boolean ka = hc.isKeepingAlive();          // true: HTTP/1.1, no "Connection: close"
try { inputStream.close(); } catch ...     // ChunkedInputStream.close() -> closeUnderlying()
if (ka) { hc.closeIdleConnection(); }       // evicts ONE idle cached conn to 127.0.0.1:8000
```

### Step A — `ChunkedInputStream.close()` → `closeUnderlying()` (ChunkedInputStream.java:213)

```
if (!error && state == STATE_DONE) { hc.finished(); }     // stream fully consumed -> cache path
else { if (!hurry()) { hc.closeServer(); } }              // not consumed -> try hurry, else hard close
```

Since the relay broke before the terminator, `state != STATE_DONE`, so `hurry()` runs.
`ChunkedInputStream.hurry()` (line 785) succeeds **only if the remaining bytes (the `0\r\n\r\n`
terminator) are already readable without blocking**. This is a sub-millisecond race with Starlette's
write:

- **hurry() succeeds** (terminator already in the socket buffer — the common case at low/moderate
  load): `hc.finished()` → `HttpClient.finished()` (HttpClient.java:440):
  `keepAliveConnections--` (starts at **5** — no `Keep-Alive: max=` header from uvicorn, HttpClient.java:934);
  if `keepAliveConnections > 0 && isKeepingAlive()` → **`putInKeepAliveCache()`** →
  `kac.put(url, null, this)`. **The socket is returned to the process-global `KeepAliveCache`,
  still ESTABLISHED, idle.**
- **hurry() fails** (terminator not yet arrived — more likely under scheduling latency at high
  concurrency): **`hc.closeServer()`** → `serverSocket.close()`. **The Gateway is the active closer
  → the local ephemeral port toward `127.0.0.1:8000` enters `TIME_WAIT` (2·MSL).**

### Step B — `if (ka) hc.closeIdleConnection()` (HttpClient.java:517)

```
HttpClient http = kac.get(url, null);   // pull ONE idle keep-alive conn for http://127.0.0.1:8000
if (http != null) http.closeServer();   // hard close it -> Gateway active closer -> TIME_WAIT
```

The JDK's own comment (HttpURLConnection.java:3110-3124) says this is deliberately an
**approximation**: "we may close a different idle connection to that used by the request … it's
possible that we close two connections."

## Answers to the audit questions

| Question | Answer from source |
|---|---|
| Does `disconnect()` immediately close *the underlying socket*? | **Not guaranteed, and not necessarily *this* socket.** Step A may return this request's socket to the cache; Step B then closes *some* idle socket to the same host (possibly this one, possibly another). In aggregate ≈ one Gateway-initiated close per request, but not a synchronous close of the specific connection. |
| Can the connection be returned to the keep-alive cache? | **Yes** — Step A's `hurry()`-succeeds branch calls `hc.finished()` → `putInKeepAliveCache()`. `setUseCaches(false)` does **not** prevent this (see below). |
| Is reuse possible on the next request? | **Yes.** `KeepAliveCache kac` is `static` (process-global), keyed by `(protocol, host, port)`. The next `openStream()` to the same URL calls `HttpClient.New()` → `kac.get(url)` first; if an idle entry exists at that instant it is reused (no new socket, no new ephemeral port). `disconnect()` does not poison the cache for the host — it only evicts up to one entry. Under concurrency the cache is frequently non-empty, so **partial keep-alive reuse genuinely occurs between requests even though every finished request calls `disconnect()`.** A given physical connection can be reused up to ~5 times (`keepAliveConnections` init = 5) before `finished()` closes it instead of re-caching. |
| Read body to EOF, *then* disconnect — which path? | Would take Step A's `state == STATE_DONE` → `hc.finished()` → cache. **But the relay never does this** — it breaks on `event: final`, so `state != STATE_DONE` and the `hurry()`-race branch is taken instead. |
| Is `disconnect()` equivalent to `Connection: close`? | **No.** `Connection: close` is a request/response header that makes the server close and makes the JDK never cache the connection at all. `disconnect()` is a post-hoc client teardown that evicts *one* cache entry. The request here goes out as HTTP/1.1 default keep-alive. |
| Does it force a per-request new outbound TCP tuple? | **No, not by contract.** It *tends* toward roughly one active-close per request for this chunked / break-before-terminator workload (Step A hard-close race + Step B eviction), which drives steady `TIME_WAIT` accumulation on Gateway-side ephemeral ports toward `127.0.0.1:8000`. It does **not** guarantee a fresh tuple every time, and measurable keep-alive reuse offsets part of the churn. |

## `setUseCaches(false)` — what it actually disables

`URLConnection.useCaches` gates the **`java.net.ResponseCache`** (HTTP response-body caching), read in
`HttpURLConnection.getInputStream0()` before `getFromCache()` / `putInCache()`. It is **unrelated to
the socket-level `sun.net.www.http.KeepAliveCache`**. Setting it `false` does **not** disable
connection keep-alive or reuse. (Common confusion; called out here so the relay's `disconnect()` is
not mistaken for "keep-alive fully disabled".)

## Where `BindException: Can't assign requested address` is thrown

`EADDRNOTAVAIL` on an outbound `HttpURLConnection` to a fixed remote with **no explicit local bind**
is the kernel refusing to auto-assign a free ephemeral source port during `connect()`. Call path:

`HttpURLConnection.getInputStream0()` → `connect()` → `HttpClient.New()` (cache miss) →
`new HttpClient(...)` → `HttpClient.openServer()` (HttpClient.java:1136 area) →
`NetworkClient.doConnect()` → `new Socket(...)` / `SocketImpl.connect()` → native `connect(2)`
returns `EADDRNOTAVAIL`.

So the failing layer is **the implicit source-port bind performed inside `connect()`**, not an
explicit `Socket.bind()` call, and not `getInputStream()`'s parsing. The relay catches it as
`IOException` and logs only `e.toString()` — **no stack trace is captured in
`m2-r256-screen1/gateway.log`** (see `bindexception-audit.md`). A fresh diagnostic run with
stack-trace logging (or `-Djdk.httpclient`/`sun.net` finest logging) is required to confirm the
frame empirically rather than by source reasoning.

## Net conclusion for the R=256 question

The Gateway→Mock outbound path is **"churning with partial keep-alive reuse and aggressive
per-request cache eviction"**, not "one clean new port per request" and not "fully pooled". The
source establishes the *mechanism* by which the Gateway becomes a frequent active-closer against a
single destination (`127.0.0.1:8000`), and therefore a genuine independent consumer of host
ephemeral ports / TIME_WAIT slots. **It does not tell us where the equilibrium sits at R=256** —
that is an empirical question the Unit 8.1 live two-leg socket telemetry (§8–§9, §17) must answer.
This is exactly why the governing instruction requires runtime evidence and forbids closing the
verdict on a source read alone.
