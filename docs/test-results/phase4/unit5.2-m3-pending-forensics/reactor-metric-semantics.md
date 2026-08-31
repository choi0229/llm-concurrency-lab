# Reactor Netty 1.3.6 — `reactor_netty_connection_provider_pending_connections` semantics

Source: bytecode inspection of the exact jar this build resolves —
`io.projectreactor.netty:reactor-netty-core:1.3.6`
(`~/.gradle/caches/modules-2/files-2.1/io.projectreactor.netty/reactor-netty-core/1.3.6/f3dd398789a99982ee3cb4c9a29c31b689173313/reactor-netty-core-1.3.6.jar`).
No sources/javadoc jar was cached locally, so this is derived directly from the released class
files via `javap`, not from a blog/doc that could describe a different version. No official
Reactor Netty documentation was fetched or relied on beyond this.

## Meter identity

`reactor/netty/resources/ConnectionProviderMeters.class` defines an enum with constants
`PENDING_CONNECTIONS`, `MAX_PENDING_CONNECTIONS`, `PENDING_CONNECTIONS_TIME`,
`TOTAL_CONNECTIONS`, etc. `PENDING_CONNECTIONS` is the meter backing the metric observed as
`reactor_netty_connection_provider_pending_connections` after Prometheus name normalization —
confirmed this is the correct, single, intended meter (not a coincidental name collision).

## What the value actually is

`reactor/netty/resources/ConnectionPoolMetrics.java` (interface, decompiled signature):

```java
public interface ConnectionPoolMetrics {
    int acquiredSize();
    int allocatedSize();
    int idleSize();
    int pendingAcquireSize();
    int maxAllocatedSize();
    int maxPendingAcquireSize();
}
```

`DelegatingConnectionPoolMetrics` (the concrete implementation wired to the gauge) delegates
every one of these directly to the shaded `reactor.pool.InstrumentedPool.PoolMetrics` from the
(shaded) reactor-pool library — i.e. `PENDING_CONNECTIONS` is a live gauge over
`pool.metrics().pendingAcquireSize()`.

**`pendingAcquireSize()` is exactly**: the number of `acquire()` calls currently queued in the
pool because the pool could not immediately hand back an idle/available connection (and, if not
already at `maxAllocatedSize()`, a new connection is being established to satisfy one of them).
This is confirmed structurally by `DefaultPooledConnectionProvider$PendingConnectionObserver`,
whose `pendingQueue` is populated exactly on `onStateChange`/`onUncaughtException` callbacks for
an in-flight connection attempt still being observed — i.e. entries in this queue are literally
"acquire calls not yet satisfied."

## What it is NOT

Bytecode gives no basis for the following, and none of them are separately named as sources of the
`PENDING_CONNECTIONS` gauge value:

- background health checks
- idle-connection eviction/replacement
- DNS resolution
- connection close/teardown bookkeeping

There is exactly one meaning for this gauge: **count of acquire() calls currently waiting for a
connection.** The earlier screening report's phrase "pool idle connection 교체/health check일
수도 있음" was speculation without this evidence and is **retracted** — the mechanism is
acquire-wait only, per §9 above.

## Implication for this run

A single second where `pendingAcquireSize()==1` genuinely means: at that Prometheus scrape
instant, exactly one WebClient `acquire()` call for the Mock-LLM pool was waiting because no idle
connection existed and (per the timeline below) a new connection was still being established.
This is the expected, textbook signature of pool ramp-up from a near-empty pool to N simultaneous
connections — not a sign of a closed/at-capacity pool (that would show `pendingAcquireSize()` at
or near the full outstanding-request count, sustained, with `maxAllocatedSize()` reached).
