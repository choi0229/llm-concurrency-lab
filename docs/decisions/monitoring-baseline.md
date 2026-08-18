# ADR: Monitoring Overhead Baseline

Status: Accepted (Phase 1 scope)
Date: 2026-08-10

목적: 모니터링/계측 자체가 실험 결과를 왜곡하는 Confounding Factor가 되지 않도록,
모든 Gateway 구현체에 걸쳐 동일하게 고정하는 계측 조건을 못박는다.

## 1. Prometheus Scrape 설정

- `scrape_interval: 5s` — 전 구현체, 전 Phase 공통 고정값
- 선택 근거: k6 측정 구간(5분 이상)에서 최소 60회 이상 샘플을 확보하면서, 너무 잦은 scrape로 인한
  애플리케이션 측 `/metrics` 렌더링 오버헤드가 무시할 수준이 되는 지점으로 판단. 구현 중 실측 후
  `/metrics` 응답 생성 시간이 5ms를 초과하면 이 값을 재검토하고 이 문서에 근거를 갱신한다.
- `scrape_timeout`: Prometheus 기본값 사용 (scrape_interval보다 작게 자동 설정), 구현체별로 다르게 두지 않는다.

## 2. 클라이언트 측 계측 라이브러리 정책 — 수정됨

**정정: Baseline은 Dropwizard MetricRegistry를 쓰지 않는다.** `docs/decisions/version-compatibility.md` 구현 중
Dropwizard 방식은 metric에 label 차원이 없어 이 프로젝트가 요구하는 `mode="pool"/"caller"` 같은 label 기반
metric을 표현할 수 없다는 것이 확인되어, **`io.prometheus:simpleclient`(0.16.0) 네이티브 API를 직접 사용**하는
것으로 변경했다(Counter/Gauge/Histogram, label 직접 지원).

- Histogram은 Dropwizard의 Reservoir 샘플링이 아니라 **simpleclient 네이티브 Histogram의 고정 bucket
  boundary**를 쓴다(`GatewayMetrics`에 명시적으로 선언). 이후 다른 Java 구현체(Virtual Thread, WebClient,
  WebFlux 등)에서도 **동일한 bucket boundary**를 유지해야 구현체 간 p95/p99 비교가 의미를 가진다 — bucket
  경계가 다르면 Prometheus의 `histogram_quantile()` 보간 결과가 구현체마다 다른 방식으로 왜곡되기 때문.
- **2026-08-13 재보정**: Unit 6 정식 결과 검토 중 `gateway_ttfb_seconds`/`executor_queue_wait_seconds`의
  원래 bucket 경계(`..., 10, 30, 60`)가 이 Baseline의 실제 관측 구간(수 초~약 60초, `CHAT_TOTAL_TIMEOUT_MS`)에서
  **너무 성겨서** Prometheus classic histogram의 `histogram_quantile()`이 10~30초, 30~60초 구간을
  선형보간하며 큰 오차를 냈다(k6 자체 계산 `client_ttfc_seconds`와 직접 비교해 확인, 상세는
  `docs/test-results/phase1/unit6-metric-calibration.md`). **1초~60초 구간의 bucket 수를 8개→22개로
  늘려 재보정**했다(`GatewayMetrics.FINE_LATENCY_BUCKETS`) — 이 Baseline 이후 구현체도 이 새 경계를
  그대로 따른다. Prometheus 공식 문서가 classic histogram의 `histogram_quantile()`이 bucket 내부에서
  균등분포를 가정해 선형보간한다는 것을 명시하고 있으므로([Prometheus 공식 문서:
  Histograms and summaries](https://prometheus.io/docs/practices/histograms/)), bucket이 성길수록
  이 가정이 깨질 위험이 커진다는 점을 일반 원칙으로 기록해 둔다.
- Python(`prometheus-client`) 쪽 Histogram도 마찬가지로 고정 bucket 방식이며, Java 쪽과 지표 성격이
  다르므로(Client 지표 vs Server 지표) 직접 수치 비교 대상이 아님을 문서에 명시한다.

## 3. 추가 계측(NMT, JFR)에 대한 기본 정책

- Phase 1 기본 측정에서는 **JFR/NMT를 켜지 않는다** (계측 자체의 오버헤드를 기준선에 섞지 않기 위함).
- NMT/JFR이 필요한 실험(예: Phase 2 Virtual Thread Pinning 검증)은 **별도의 독립된 실행**으로 분리하고,
  "계측 On/Off에 따른 성능 차이"를 그 자체로 하나의 결과로 문서화한다. 기본 벤치마크 결과와 섞지 않는다.

## 4. 로깅 정책

- 전 구현체 공통 로그 레벨: `INFO`
- 요청 단위 상세 로그(`DEBUG`)는 기본적으로 비활성화. 필요 시 별도 실행으로 "로깅 오버헤드 실험"을 분리해서 진행한다.
- 로그에는 Correlation ID(Baseline은 요청 단위 ID, RabbitMQ는 Job ID)를 포함하되, 민감정보는 남기지 않는다.

## 5. 실행마다 기록할 모니터링 조건 메타데이터

각 테스트 run의 `environment.json`에 다음을 함께 기록한다.

```
- prometheus_scrape_interval_seconds
- log_level
- jfr_enabled (true/false)
- nmt_enabled (true/false)
- metrics_library (예: io.prometheus:simpleclient:0.16.0, 네이티브 API)
```

## 6. Histogram Bucket Schema — Phase 2 이후 비교를 위한 최종 schema (freeze, Unit 6.7)

Status: Frozen — Phase 1 formal run 재실행 없음, Phase 2 이후 새 구현체 착수 시 코드에 적용.
Date: 2026-08-15

**배경**: §2의 2026-08-13 재보정(`FINE_LATENCY_BUCKETS`, 1초~60초 구간 8→22개)은 지연이 큰(수 초~수십
초) 구간의 quantile 왜곡을 크게 줄였다(Unit 6 §5-4, B-R2 calibration: TTFB/QueueWait와 TTFC 간 격차가
약 12초 → 약 1초). 하지만 지연이 작은(1~2초) 구간에서는 여전히 상대오차가 남았다 — 현재 schema가 0.5초와
2초 사이에 `1`, `2` 두 지점뿐이라 그 구간 내부에서 여전히 성기게 선형보간되기 때문이다(Unit 6 §5-4,
E-R5 calibration: client TTFC ≈ 1.01s vs gateway TTFB ≈ 1.95s).

**조치**: 0.5초~3초 구간에 아래 지점을 추가해, 기존 sub-1초 구간(`..., 0.1, 0.25, 0.5`)이나 3초 이후
구간(`3, 4, 5, ...`)과 겹치지 않게 세분화한다.

```
기존(1초~60초, 2026-08-13 재보정):
1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 18, 21, 24, 27, 30, 35, 40, 45, 50, 55, 60

Phase 2 이후 최종 schema(0.5초~3초 구간만 교체, 나머지 sub-0.5초/3초 이후 구간은 변경 없음):
0.75, 0.9, 1.0, 1.05, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15, 18, 21, 24, 27, 30,
35, 40, 45, 50, 55, 60
```

즉 전체 `FINE_LATENCY_BUCKETS`(Phase 2용, 코드에 적용 시 이 순서 그대로):

```
0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5,
0.75, 0.9, 1.0, 1.05, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5,
3, 4, 5, 6, 7, 8, 10, 12, 15, 18, 21, 24, 27, 30, 35, 40, 45, 50, 55, 60
```

**적용 범위 및 시점**: 이 schema는 **Phase 1의 기존 24개 formal run에는 소급 적용하지 않는다** — 그
run들의 원본 데이터는 이미 수집된 구 schema 기준이며, 재실행 없이 문서만 정리하는 것이 Unit 6.7의
scope다(`docs/test-results/phase1/unit6-formal-benchmark-results.md` §5-6 참고). Phase 2에서 새
구현체(Virtual Thread, WebClient, WebFlux 등)를 벤치마크할 때 `GatewayMetrics.FINE_LATENCY_BUCKETS`를
이 schema로 갱신하고, 그 시점부터 수집되는 모든 run(Phase 1 재실행분 포함, 재실행하는 경우)이 이 schema를
따른다. §2의 "동일 bucket boundary를 유지해야 구현체 간 비교가 의미를 가진다" 원칙에 따라, 이 schema를
채택한 이후에는 Phase 2 전체가 이 schema로 고정된다 — 구현체별로 다시 바꾸지 않는다.

client-side k6 Trend(`client_ttfc_seconds`, `client_ttfc_completed_seconds` 등)는 이 재보정과 무관하게
계속 user-facing latency의 source of truth로 유지한다 — bucket 재보정은 server-side histogram의 근사
정확도를 개선하는 것이지, client-side 원본 표본 기반 계산을 대체하는 것이 아니다.
