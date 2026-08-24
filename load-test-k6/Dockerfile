# Custom k6 binary with xk6-sse statically linked in, built via the official
# grafana/xk6 build tool. Chosen over k6's runtime "auto-extension-resolution"
# (k6 v1.2.0+) for reproducibility: this bakes an exact, pinned k6-core +
# xk6-sse combination into the image at build time with no network dependency
# or on-demand compilation during an actual benchmark run — see
# docs/decisions/load-generator.md.
#
# Pinned by digest (linux/arm64 manifest verified 2026-08-10) — xk6 tool
# version inside this image at that digest: 1.4.9 (`xk6 version`).
FROM grafana/xk6@sha256:6e910bcf732e7611ca69fce51d9587ea3fed0546d08f384901d1c456d90ef553 AS build
WORKDIR /xk6
# xk6-sse@v0.1.11's go.mod pins go.k6.io/k6 v1.1.0 (the pre-v2 Go module
# path). k6 v2.0.0 renamed the module path to go.k6.io/k6/v2, so building
# against a v2.x core silently breaks the extension: `xk6 build v2.2.0
# --with github.com/phymbert/xk6-sse@v0.1.11` completes without error but
# the resulting binary fails at runtime — `import sse from 'k6/x/sse'`
# throws "unknown dependency: k6/x/sse" (auto-extension-resolution also
# fails to recover it). v1.8.0 is the last k6 release on the old module
# path; building against it is not an "officially recommended combination"
# from any vendor doc — it is the exact combination this project actually
# built, ran, and verified end-to-end (Docker build, `k6/x/sse` import,
# POST+SSE response, `client.on('event', ...)`, `client.close()` propagating
# a real upstream cancellation) — see docs/decisions/load-generator.md.
RUN xk6 build v1.8.0 --with github.com/phymbert/xk6-sse@v0.1.11 -o /xk6/k6

# Pinned by digest (linux/arm64 manifest verified 2026-08-10).
FROM debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /xk6/k6 /usr/bin/k6
WORKDIR /scripts
ENTRYPOINT ["k6"]
