#!/usr/bin/env bash
# Phase 2 Native macOS ARM64 Formal — runtime dependency setup.
#
# Downloads/verifies/builds the exact pinned runtime artifacts used by
# scripts/run-phase2-native-formal-benchmark.sh into .native-runtime/
# (gitignored, ~1.3GB, never committed) and (re)creates mock-llm-fastapi's
# .venv with the exact pinned Python package versions. Idempotent — safe to
# re-run; skips a step if its artifact already exists with the correct
# checksum/version.
#
# See docs/decisions/phase2-formal-native-macos-environment.md for why these
# exact versions/checksums were chosen (same patch versions as the Docker
# Formal environment, different distribution artifact — Docker image digests
# don't apply to native macOS binaries).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE_DIR="${ROOT}/.native-runtime"
mkdir -p "${NATIVE_DIR}"
cd "${NATIVE_DIR}"

echo "=== [1/4] Eclipse Temurin 21.0.11+10 (macOS aarch64 JDK) ==="
JDK_DIR="${NATIVE_DIR}/jdk-21.0.11+10"
JDK_SHA="6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201"
if [ -x "${JDK_DIR}/Contents/Home/bin/java" ]; then
  echo "already present: ${JDK_DIR}"
else
  curl -sL -o jdk21.tar.gz \
    "https://api.adoptium.net/v3/binary/version/jdk-21.0.11%2B10/mac/aarch64/jdk/hotspot/normal/eclipse?project=jdk"
  ACTUAL_SHA=$(shasum -a 256 jdk21.tar.gz | awk '{print $1}')
  if [ "${ACTUAL_SHA}" != "${JDK_SHA}" ]; then
    echo "FATAL: JDK checksum mismatch (expected ${JDK_SHA}, got ${ACTUAL_SHA})" >&2
    exit 1
  fi
  tar xzf jdk21.tar.gz
  rm -f jdk21.tar.gz
fi
JDK_HOME="${JDK_DIR}/Contents/Home"
"${JDK_HOME}/bin/java" -version

echo ""
echo "=== [2/4] Prometheus 3.13.2 (darwin-arm64) ==="
PROM_DIR="${NATIVE_DIR}/prometheus-3.13.2.darwin-arm64"
PROM_SHA="f68ca4f1dbedd6366bbfdd8ac5d2c0b7ba1f273474acc8d38eb33202fbeec7a4"
if [ -x "${PROM_DIR}/prometheus" ]; then
  echo "already present: ${PROM_DIR}"
else
  curl -sL -o prometheus.tar.gz \
    "https://github.com/prometheus/prometheus/releases/download/v3.13.2/prometheus-3.13.2.darwin-arm64.tar.gz"
  ACTUAL_SHA=$(shasum -a 256 prometheus.tar.gz | awk '{print $1}')
  if [ "${ACTUAL_SHA}" != "${PROM_SHA}" ]; then
    echo "FATAL: Prometheus checksum mismatch (expected ${PROM_SHA}, got ${ACTUAL_SHA})" >&2
    exit 1
  fi
  tar xzf prometheus.tar.gz
  rm -f prometheus.tar.gz
fi
"${PROM_DIR}/prometheus" --version

echo ""
echo "=== [3/4] k6 v1.8.0 + xk6-sse v0.1.11 (native darwin/arm64 build via xk6) ==="
K6_BIN="${NATIVE_DIR}/k6"
if [ -x "${K6_BIN}" ] && "${K6_BIN}" version 2>/dev/null | grep -q "xk6-sse v0.1.11"; then
  echo "already present: ${K6_BIN}"
else
  if ! command -v go >/dev/null 2>&1; then
    echo "FATAL: Go toolchain not found — install with 'brew install go' first" >&2
    exit 1
  fi
  export GOPATH="${NATIVE_DIR}/go"
  mkdir -p "${GOPATH}"
  go install go.k6.io/xk6/cmd/xk6@latest
  "${GOPATH}/bin/xk6" build v1.8.0 --with github.com/phymbert/xk6-sse@v0.1.11 -o "${K6_BIN}"
fi
"${K6_BIN}" version

echo ""
echo "=== [4/4] mock-llm-fastapi native venv (Python 3.12, pinned requirements) ==="
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "FATAL: python3.12 not found — install with 'brew install python@3.12' first" >&2
  exit 1
fi
MOCK_DIR="${ROOT}/mock-llm-fastapi"
rm -rf "${MOCK_DIR}/.venv"
python3.12 -m venv "${MOCK_DIR}/.venv"
"${MOCK_DIR}/.venv/bin/pip" install --upgrade pip -q
"${MOCK_DIR}/.venv/bin/pip" install -r "${MOCK_DIR}/requirements.txt" -q
"${MOCK_DIR}/.venv/bin/python3" --version
"${MOCK_DIR}/.venv/bin/pip" freeze > "${NATIVE_DIR}/mock-llm-pip-freeze.txt"
echo "pip freeze saved to ${NATIVE_DIR}/mock-llm-pip-freeze.txt"

echo ""
echo "=== availableProcessors() helper (tiny compiled class, no Formal semantics) ==="
mkdir -p "${NATIVE_DIR}/tools"
cat > "${NATIVE_DIR}/tools/AvailableProcessors.java" <<'EOF'
public class AvailableProcessors {
    public static void main(String[] args) {
        System.out.println(Runtime.getRuntime().availableProcessors());
    }
}
EOF
"${JDK_HOME}/bin/javac" -d "${NATIVE_DIR}/tools" "${NATIVE_DIR}/tools/AvailableProcessors.java"

echo ""
echo "=== Setup complete ==="
echo "JDK_HOME=${JDK_HOME}"
echo "PROMETHEUS_BIN=${PROM_DIR}/prometheus"
echo "K6_BIN=${K6_BIN}"
echo "availableProcessors()=$("${JDK_HOME}/bin/java" -cp "${NATIVE_DIR}/tools" AvailableProcessors)"
