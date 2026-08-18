# ADR: Load Generator (k6) — SSE Support Decision

Status: Accepted (Phase 1 / Unit 4 scope)
Date: 2026-08-10

근거 우선순위: 공식 Grafana k6 문서 → 해당 extension(xk6-sse) 공식 GitHub repository → 실제 빌드/실행으로
직접 검증한 결과. 커뮤니티 포럼/블로그는 방향을 잡는 데만 참고하고 결정 근거로 쓰지 않았다.

## 1. Core k6 HTTP API로 SSE를 재현할 수 있는가 — 아니오

k6 core `http.post()`/`http.get()`은 응답 전체를 받은 뒤 반환하는 완전 동기(blocking) 호출이다. 응답을
event/chunk 단위로 순차 소비하는 API가 core `http` 모듈에 없다. Grafana k6 저장소의
[Issue #746 "HTTP SSE (Server-Sent Event) support for k6"](https://github.com/grafana/k6/issues/746)가
core에 SSE 지원이 없다는 것 자체를 보여주는 근거다. 따라서 이 프로젝트가 요구하는 3가지(비버퍼링 event 단위
수신, 첫 event 도착 시각의 직접 측정, 스트리밍 도중 의도적 연결 종료)는 core API만으로 구현할 수 없다 —
억지로 `http.post()`로 구현하지 않는다.

## 2. xk6-sse 검토 및 채택

[`github.com/phymbert/xk6-sse`](https://github.com/phymbert/xk6-sse)는 [Grafana k6 공식 Extension
Registry](https://grafana.com/docs/k6/latest/extensions/explore/)에 Community Extension으로 등재되어
있다(현재 `v0.1.11`). API:

```js
import sse from 'k6/x/sse'
sse.open(url, { method: 'POST', body: ... }, function (client) {
  client.on('open', function () {})
  client.on('event', function (event) {})   // event.id, event.name, event.data
  client.on('error', function (e) {})
  client.close()                             // 실제 연결 종료
})
```

`k6/metrics`의 `Trend`/`Counter`/`Rate`를 `client.on('event', ...)` 콜백 내부에서 그대로 사용할 수 있다
(공식 예제가 LLM streaming 벤치마크에 이 패턴을 그대로 씀).

## 3. 커스텀 바이너리 필요 여부 — 실제로 검증하며 문제를 발견/수정함

k6 v1.2.0+는 "automatic extension resolution" 기능으로 `import ... from 'k6/x/sse'`를 감지하면 커스텀
빌드 없이도 온디맨드로 provisioning을 시도한다(`K6_ENABLE_COMMUNITY_EXTENSIONS=true` 필요). 이 프로젝트는
재현성을 위해 **`grafana/xk6`로 사전에 정적 링크된 커스텀 바이너리를 빌드**하는 방식을 채택했다 — 벤치마크
실행 중에 네트워크 의존적인 온디맨드 컴파일이 끼어들지 않게 하기 위함이다.

**빌드 중 실제로 발견한 호환성 문제 (공식 소스만으로는 안 드러났던 부분, 직접 빌드/실행 검증으로 발견):**

- xk6-sse **v0.1.11**의 `go.mod`(태그 `v0.1.11`을 직접 fetch해 확인, `main` 브랜치가 아님)는
  `go.k6.io/k6 v1.1.0`(k6 v2.0.0 이전의 옛 Go module 경로)을 참조한다. — *이전 버전 문서에서 이 값을
  `v1.6.1`로 잘못 기록했었다. `main` 브랜치의 go.mod를 확인한 것이 원인이었고, 실제 사용할 태그
  (`v0.1.11`)의 go.mod를 다시 확인해 정정함.*
- k6 v2.0.0부터 Go module 경로가 `go.k6.io/k6` → `go.k6.io/k6/v2`로 바뀌었다(공식 GitHub Releases에
  명시). 이 때문에 **옛 module 경로(`go.k6.io/k6`)를 참조하는 xk6-sse를 새 module 경로(`go.k6.io/k6/v2`)
  기반 k6 v2.x 코어와 조합하면 Go module 시스템이 두 경로를 서로 다른 모듈로 인식**해 extension이 제대로
  링크되지 않는다.
- 처음 시도한 k6 v2.2.0(당시 검색 결과에서 "최신 stable"로 보였던 버전 — 이 값 자체를 `k6 version`으로
  직접 검증하지 않은 채 기록했던 것이 문제였음, 지금은 이 문서에서 "최신 버전"이라고 단정하지 않는다)으로
  `xk6 build v2.2.0 --with github.com/phymbert/xk6-sse@v0.1.11`을 실행하면 **빌드 자체는 성공**하지만,
  `conflicting k6 versions detected ... extensions depending on go.k6.io/k6 will not be active`라는
  경고가 뜨고, 실제로 스크립트를 실행하면 `import sse from 'k6/x/sse'`가
  **`unknown dependency: k6/x/sse`로 런타임에 실패**했다 (auto-extension-resolution 재시도까지도 실패).
- k6 **v1.8.0**(2024-06-08, v2.0.0 이전 마지막 릴리스, 옛 module 경로 유지)로 다시 빌드하니 경고 없이
  빌드되고, `import sse from 'k6/x/sse'`가 정상 동작함을 실제 실행으로 확인했다.

**결론: k6 core `v1.8.0` + `xk6-sse@v0.1.11`을 `grafana/xk6`(linux/arm64 native)로 정적 빌드한 커스텀
바이너리를 사용한다.** 이 조합은 **"공식 권장 조합"이 아니다** — Grafana나 xk6-sse 어느 쪽도 이 정확한
조합을 문서에서 권장한 적이 없다. 이 조합을 채택한 이유는 오직 **Docker build → `k6/x/sse` import →
POST+SSE 응답 수신 → `client.on('event', ...)` 콜백 → `client.close()`에 의한 실제 upstream cancellation
전파까지, 이 프로젝트가 직접 빌드하고 실행해서 end-to-end로 실측 검증한 조합이기 때문**이다(4번 항목 결과
참고). k6 core는 이후 v2.x 라인에서 계속 발전하고 있지만, xk6-sse가 아직 그 module 경로 전환을 따라가지
못했으므로 **이 프로젝트의 SSE 스트리밍 테스트는 k6 v1.8.0에 고정된다** — 이는 명백한 한계이며 5번 항목에
기록한다.

## 4. 실제 빌드 + 실행 검증 결과 (2026-08-10, Docker linux/arm64, 에뮬레이션 없음)

`load-test-k6/Dockerfile`: `grafana/xk6`(build stage, digest pin)로 `xk6 build v1.8.0 --with
github.com/phymbert/xk6-sse@v0.1.11`, 결과 바이너리를 `debian:bookworm-slim`(runtime stage, digest pin)
이미지에 복사. 재현성을 위해 두 stage 모두 `:latest`/floating tag가 아니라 **digest로 고정**한다(아래
메타데이터 표).

**Load Generator 메타데이터 (재현/기록용, 추측 아님 — 전부 실제 명령 출력):**

| 항목 | 값 | 확인 명령 |
|---|---|---|
| k6 version | `k6 v1.8.0 (go1.26.5, linux/arm64)` | `docker run --rm k6-sse:dev version` |
| xk6-sse version | `v0.1.11` (binary가 스스로 보고: `Extensions: github.com/phymbert/xk6-sse v0.1.11, k6/x/sse [js]`) | 위와 동일 명령 출력 |
| xk6 builder version | `1.4.9` | `docker run --rm --entrypoint sh grafana/xk6@sha256:6e910b... -c "xk6 version"` |
| build stage image | `grafana/xk6@sha256:6e910bcf732e7611ca69fce51d9587ea3fed0546d08f384901d1c456d90ef553` | `docker image inspect grafana/xk6:latest --format '{{.RepoDigests}}'` |
| runtime base image | `debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241` (bookworm-slim) | `docker image inspect debian:bookworm-slim --format '{{.RepoDigests}}'` |
| 최종 이미지 ID | `sha256:69fc5033d1b584ee264f80664ee05e389b944fc8089e68199e5eb1e0db5778af` | `docker image inspect k6-sse:dev --format '{{.Id}}'` |
| container architecture | `aarch64` (arm64 native, 에뮬레이션 없음) | `docker run --rm --entrypoint uname k6-sse:dev -m` |

이 표의 값들은 이후 각 Benchmark run의 `environment.json`에도 그대로 기록한다.

mock-llm-fastapi(`chunkCount=6, chunkIntervalMs=200ms, firstChunkDelayMs=100ms`)를 대상으로 실제 스크립트
(`load-test-k6/sse-verification-test.js`)를 실행해 3가지 요구사항을 모두 실측 확인:

| 요구사항 | 검증 방법 | 결과 |
|---|---|---|
| 1. Event 단위 비버퍼링 수신 | 각 event 수신 시각을 `Date.now()`로 기록 | `[107ms, 309ms, 509ms]` — `chunkIntervalMs=200ms` 간격으로 순차 도착. 응답 종료까지 기다렸다가 한 번에 오지 않음 |
| 2. Client 측 TTFC 직접 측정 | 첫 `event` 콜백 시각 - 요청 시작 시각 | `client_ttfc_seconds ≈ 0.107s` — `firstChunkDelayMs=100ms`와 일치 |
| 3. 의도적 Client Disconnect | 3번째 event에서 `client.close()` 호출 | 클라이언트는 6개 중 3개만 수신하고 중단. **Mock LLM 쪽 `mockllm_cancelled_requests_total`이 1 증가**(`completed=0`, `active_requests=0`) — `close()`가 실제로 TCP 연결까지 끊어 서버까지 취소가 전파됨을 확인 |

부가 발견: `sse.open()` 호출 자체가 표준 k6 HTTP 요청으로도 계측되어, 별도 스크립트 없이도
`http_req_duration`, `http_reqs`, `iterations`, VU 등 **Track A 지표가 SSE 스크립트 하나에서 함께
나온다** (검증 실행 결과에 `http_req_duration`, `http_reqs`, `iterations` 모두 자동 출력됨). 따라서 Phase 1의
세 Scenario(1/2/6)는 모두 event 단위 계측이 필요하므로, **Track A 전용 스크립트를 별도로 두지 않고 xk6-sse
기반 스크립트 하나로 Track A+B 지표를 동시에 확보**한다. Track A만 필요한 순수 커넥션 테스트가 향후 Phase에서
필요해지면 그때 core `http` API로 별도 작성한다.

## 5. 한계 (Limitations)

- SSE 테스트는 k6 **v1.8.0**(2024-06-08)에 고정되며, k6 core의 최신 기능(v2.x 라인)을 못 받는다. xk6-sse가
  `go.k6.io/k6/v2`로 갱신되면 재검토한다.
- xk6-sse는 Grafana 공식 유지보수 대상이 아닌 Community Extension이다(등재는 공식 Registry에 되어 있으나
  유지보수는 개별 관리자 `phymbert`가 함). 향후 k6 버전과의 호환성이 다시 깨질 수 있다.
- `client.close()`가 이번 검증에서는 확실히 동작했지만, 매우 짧은 `chunkIntervalMs`(예: 개별 이벤트 간
  수 ms 이하)에서도 매번 정확히 같은 시점에 끊기는지는 별도로 확인하지 않았다 — Unit 4-4에서 실제 Scenario로
  재확인한다.
- Docker 이미지 빌드 시 `grafana/xk6`가 내부적으로 Go module proxy(`proxy.golang.org` 등)에 네트워크
  접근이 필요하다 — 오프라인 환경에서는 사전에 이미지를 빌드해 두거나 vendor 캐시가 필요하다.

## 6. 요약 (Unit 4-1 완료 기준 대응)

| 항목 | 결정 |
|---|---|
| k6 정확 버전 | `k6 v1.8.0 (go1.26.5, linux/arm64)` — `k6 version` 실행 결과 그대로, 추측 아님 |
| ARM64 지원 | `grafana/xk6`, 빌드 산출물 모두 arm64 native 확인, 에뮬레이션 없음 |
| SSE 지원 방식 | xk6-sse extension (core API 불가 확인됨) |
| xk6-sse 버전 | v0.1.11 |
| 자동 resolution vs 커스텀 바이너리 | 커스텀 바이너리 채택 (재현성, 오프라인 실행) — auto-resolution은 애초에 k6 v2.x 호환 문제로 동작하지도 않았음 |
| POST + SSE response | 확인됨 |
| close()/disconnect | 확인됨 (Mock LLM까지 취소 전파 확인) |
| custom Trend/Counter | 확인됨, `client.on('event', ...)` 내부에서 정상 동작 |
