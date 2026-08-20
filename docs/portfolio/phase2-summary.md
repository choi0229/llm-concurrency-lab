# Phase 2 Portfolio Summary — LLM Concurrency Lab

Source: `docs/test-results/phase2/phase2-final-report.md`. 이 문서는 그 보고서의 요약을 재사용 가능한
형태로 분리한 것이다 — 새 주장을 추가하지 않는다. Primary 숫자는 `_aggregated.json`(18 valid native
macOS ARM64 run) 기준 median 값이고, Secondary(VT-Unlimited Screening, JFR Pinning Diagnostic) 숫자는
`docs/test-results/phase2/secondary/`의 각 조건 1회 관찰 raw 결과다 — 반복측정 통계가 아니다.

## A. README용 (8~12줄)

Java 21 Virtual Thread가 blocking I/O 구조를 유지한 채 platform thread 비용을 실제로 줄이는지, 그리고
그 여유가 admission control을 대체할 수 있는지 실측한 Benchmark. Phase 1(Java 8)의 Executor를 그대로
두고 Chat Executor 하나만 Platform `ThreadPoolExecutor`(P-E)와 Virtual Thread(VT-Limited,
`Semaphore(50)` admission gate로 동일 concurrency ceiling 유지)로 바꿔, R3/R6/R8 부하 × 3회 반복 = 18
valid run을 측정했다. 동일 admission ceiling에서 completion throughput·rejection ratio·TTFC는 반복
측정의 변동 범위에서 실질적인 차이가 없었지만, JVM platform thread peak는 부하가 늘수록 Platform
구성은 계속 증가한 반면(47→71→73) Virtual Thread 구성은 33~35에서 거의 평평하게 유지돼 최대 약 52%
낮았다. 다만 이 thread 절감이 RSS 절감으로 일관되게 이어지지는 않았고(R3/R6은 오히려 VT가 더 높음),
CPU도 VT가 세 부하 전부에서 18~27% 더 사용했다(절대값은 둘 다 0.06 core 미만). 이어서 진행한 Secondary
실험은 두 가지를 추가로 밝혔다: JFR `jdk.VirtualThreadPinned` diagnostic은 detector가 정상 동작함을
확인한 뒤(positive control 5/5 검출) 실제 Gateway의 정상 SSE·~30초 blocking read 두 workload 모두에서
pinning을 관측하지 못했고(NOT OBSERVED UNDER TESTED CONDITIONS), admission 제한을 없앤 VT-Unlimited는
Gateway 자신을 500 concurrent까지 유지시켰지만 downstream(Mock) capacity 자체는 늘리지 못해 admission
없이는 초과 부하가 downstream waiting queue로 그대로 옮겨갔다(tail latency 최대 16.7s/23.6s). 측정
과정에서 Mac + Docker Desktop 환경의 반복적인 environment-level stall을 독립 metric으로 확인해 환경
자체를 탈락시키고, Docker를 완전히 제거한 native macOS harness로 이전한 뒤에야 18/18 valid run을
확보했다.

## B. 이력서 / 포트폴리오 Bullet (3~5개)

- Java 21 `ThreadPoolExecutor` vs `Semaphore(50)` 기반 Virtual Thread를 동일 admission concurrency
  ceiling에서 R3/R6/R8 × 3회 반복(18 valid run)으로 비교 측정 — JVM platform thread peak를 근접/과부하
  구간에서 최대 약 52%(73→35) 감소시키면서 completion throughput/TTFC는 반복 측정 범위에서 유지됨을
  실측으로 확인.
- Platform thread 절감이 RSS·CPU 절감으로 자동 연결되지 않는다는 반직관적 결과를 발견 —
  "Virtual Thread가 무조건 자원을 아낀다"는 단순화된 결론을 데이터로 반박하고, thread-model 단순화와
  memory/CPU 비용을 분리해서 보고.
- JFR `jdk.VirtualThreadPinned` diagnostic을 positive control(intentional pinning 5/5 검출로 detector
  검증) → 실제 Gateway 정상 SSE → ~30초 blocking read 3단계로 설계·실행해, 두 실제 workload 모두에서
  pinning을 관측하지 못했음을 "관측되지 않았다"와 "존재하지 않는다"를 구분해 정확하게 보고.
- VT-Unlimited(admission gate 제거) screening으로 "Gateway가 더 많은 동시 연결을 유지할 수 있다"와
  "downstream 처리량이 늘어난다"가 서로 다른 주장임을 실측으로 분리 — admission control 제거 시 초과
  부하가 사라지지 않고 downstream waiting queue/tail latency로 이동함을 확인.
- Docker Desktop 환경에서 로그 gap과 Prometheus counter plateau라는 두 독립 metric이 동일 30~35초
  구간에서 일치하는 것을 근거로 measurement environment를 공식 탈락시키고, Docker-free native macOS
  harness(fresh-process lifecycle, ps 기반 RSS sampler, Prometheus counter-plateau validity gate)를
  처음부터 설계·구현해 18/18 valid run을 확보.

## C. 면접 설명용 스크립트 (1~2분)

"Phase 1에서 Java 8 blocking Executor를 세 가지로 비교해봤는데, 결론은 blocking I/O를 유지하는 한
overload 비용은 없어지지 않고 항상 어딘가로 옮겨간다는 거였습니다. Phase 2는 그 다음 질문이었습니다
— Java 21 Virtual Thread를 쓰면 이 비용 자체를 줄일 수 있는가. 그래서 Chat Executor 하나만 Platform
스레드풀에서 Virtual Thread로 바꾸고, 나머지는 전부 동일하게 뒀습니다. 동시성 제한도 똑같이
50개로 맞췄고요. 3개 부하 구간에서 3번씩 반복해서 18번 측정했더니, 처리량이나 응답 지연은 두 방식이
거의 차이가 없었는데, JVM이 쓰는 platform thread 수는 확실히 갈렸습니다 — 부하가 늘수록 Platform
방식은 스레드가 계속 늘어나는데(최대 73개), Virtual Thread 방식은 35개 선에서 거의 평평했어요. 부하가
늘어도 스레드 수가 거의 안 늘어난다는 게 가장 뚜렷한 결과였습니다. 그런데 여기서 재밌는 게, 그 스레드
절감이 메모리 절감으로 이어지지는 않았다는 겁니다 — 오히려 낮은 부하에서는 Virtual Thread 쪽 메모리가
더 높았고, CPU도 세 구간 다 Virtual Thread가 조금씩 더 썼습니다.

그래서 저는 이 결과를 'Virtual Thread가 처리량을 늘리는 기술'이 아니라 'blocking concurrency와
platform thread 증가를 분리하는 기술'이라고 정리했습니다. 동일 admission ceiling에서는 P-E와 완료
처리량이 같았지만 JVM platform thread peak는 약 50% 줄었어요. 하지만 RSS 절감은 일관되지 않았고 CPU는
소폭 증가했습니다. 이어서 두 가지를 추가로 확인했습니다. 하나는 JFR로 실제 pinning이 관찰되는지 —
먼저 일부러 pinning을 일으키는 코드로 detector가 제대로 잡는지 검증한 다음(5건 다 잡았습니다), 실제
Gateway의 정상 스트림이랑 30초짜리 긴 blocking read 상황에서 확인해봤는데 둘 다 pinning이 관측되지
않았습니다. 다른 하나는 admission 제한을 아예 없앤 VT-Unlimited로, Gateway는 500개 동시 연결까지
문제없이 유지했지만, downstream cap을 20으로 걸어두니까 admission 없이 들어온 초과 40개가 사라지는 게
아니라 그대로 downstream 대기열로 넘어가서 최악의 경우 20초 넘게 기다려야 했습니다. 그래서 결론은
'Virtual Thread를 쓰더라도 downstream capacity에 맞춘 admission control은 여전히 필요하다'는
겁니다. 그리고 이 측정을 하는 과정 자체도 순탄치 않았습니다 — 처음에 Mac Docker Desktop 환경에서
측정했더니 로그도 조용해지고 Prometheus 카운터도 동시에 30초 넘게 멈추는 현상이 재현됐어요. 성공률만
보면 정상 같았지만, 두 독립적인 신호가 같은 구간에서 일치한다는 게 환경 문제라는 강한 증거였습니다.
그래서 그 환경을 공식적으로 탈락시키고, Docker를 아예 빼고 macOS에서 직접 실행하는 측정 harness를
새로 만들어서 최종 측정을 확보했습니다."

## 핵심 수치 (근거: `phase2-final-report.md`)

### Primary (18 valid run, median)

- R3: JVM platform thread peak 47(P-E) → 33(VT-Limited), 약 -29.8%
- **R6: JVM platform thread peak 71(P-E) → 35(VT-Limited), 약 -50.7%**
- **R8: JVM platform thread peak 73(P-E) → 35(VT-Limited), 약 -52.1%**
- R8 동일 completion throughput: P-E 6.347 RPS ↔ VT-Limited 6.350 RPS(rough capacity estimate
  50/7.85s≈6.37 RPS에 근접)
- R8 동일 rejection: P-E 20.7% ↔ VT-Limited 20.6%
- RSS: 일관된 절감 없음(R3 +5.7%, R6 +3.2%, R8만 -3.6%, 전부 VT-Limited 기준 P-E 대비)
- CPU: VT-Limited가 세 부하 전부에서 P-E보다 18~27% 높음, 단 절대값은 양쪽 모두 <0.06 core
- Formal 18 valid run 전부 `counter-plateau-check.json` 기준 20초 미만(최대 관측 10초) — Mac Docker
  Desktop 환경의 30~35초 plateau와 대비

### Secondary (각 조건 1회 screening/diagnostic)

- JFR B-0 positive control: intentional pinning 5/5 검출(duration 303~305ms) — detector pipeline
  정상 확인
- JFR B-1(정상 SSE)/B-2(~30초 blocking read), 둘 다 VIRTUAL_LIMITED concurrency=30:
  `jdk.VirtualThreadPinned` **0건** — H2-d = NOT OBSERVED UNDER TESTED CONDITIONS(다른 workload/
  concurrency로 일반화하지 않음)
- VT-Unlimited A-1(Gateway-bound): VUS 100/200/500 전부 clean(0 failed/rejected), JVM platform
  thread peak가 요청 수와 1:1로 증가하지 않음(96~128 범위)
- VT-Unlimited A-2(Downstream-bound, VUS=60, Mock cap=20): VT-Limited(permits=20)는 20 completed/
  40 rejected(Mock waiting=0, 즉시 거절); VT-Unlimited는 60 completed/0 rejected이지만 Mock
  waiting peak=40, TTFC 최대 16.7s·stream duration 최대 23.6s — **admission control은 downstream
  capacity를 늘리지 않고, excess load가 어디서 기다리거나 실패할지를 결정한다**
