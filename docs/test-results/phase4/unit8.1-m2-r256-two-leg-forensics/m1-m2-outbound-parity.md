# M1 / M2 outbound-path parity — verification

## Method

`diff -rq` of the entire shared package
`src/main/java/com/llmconcurrencylab/phase4/common/` between
`gateway-phase4-platform-queue` (M1) and `gateway-phase4-virtual-thread` (M2),
plus SHA-256 of the three files on the outbound path.

## Result — byte-identical

```
$ diff -rq gateway-phase4-platform-queue/.../phase4/common/ gateway-phase4-virtual-thread/.../phase4/common/
(no output — 15 files each, zero differences)

SHA-256:
MockLlmClient.java        778a2e65…b449c4   (M1 == M2)
BlockingMockLlmRelay.java dc878cf3…ab71b9   (M1 == M2)
RequestLifecycle.java     9336883d…686687ba  (M1 == M2)
```

Every file in `common/` — `MockLlmClient`, `BlockingMockLlmRelay`, `ChatController`,
`RequestLifecycle`, `PerStreamWriteChannel`, `DeadlineWatchdog`, `Outcome`, … — is identical.

## Implication (per governing instruction §3, §22)

The outbound `HttpURLConnection` behaviour observed at R=256 (`disconnect()`-per-request against
`127.0.0.1:8000`, the `KeepAliveCache` interaction, and the 9 `BindException`s) is a property of the
**shared blocking `HttpURLConnection` outbound path used identically by M1 and M2**, driven by
whichever executor runs `relay()` (M1 = `ThreadPoolExecutor`, M2 = `VirtualThreadPerTaskExecutor`).
It is **not** a `java.lang.VirtualThread` API property.

Therefore, if the Unit 8.1 verdict turns out to be Case C (blocking-outbound architecture resource
boundary), the correct wording is **"M2's blocking `HttpURLConnection` outbound connection-management
path reaches an OS transport-resource boundary"**, never "Virtual Thread scheduler limit". The same
code path exists in M1; M1 simply never reaches R=256 because its `PlatformTaskSubmitter` rejection
boundary (`PT_WORKER_COUNT=50`, `PT_QUEUE_CAPACITY=500`) fires far earlier (known-RED at R=10,
`MODEL_REJECTION`, reproduced again in Unit 8). This must not be described as "a defect that exists
only in M2".

M3 (`gateway-phase4-webflux`) shares **none** of `common/` — it has its own
`ChatHandler`/`WebClientConfig` using a pooling-aware Reactor Netty `WebClient` for outbound, so its
R=256 outbound behaviour is expected to differ and is a separate question (§23).
