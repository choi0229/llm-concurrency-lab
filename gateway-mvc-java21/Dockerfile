# Build and runtime pinned to an EXACT JDK 21 patch (Eclipse Temurin) — Phase 2
# primary runtime, see docs/decisions/version-compatibility.md section 6 and
# docs/test-plan/phase2-design.md section 6 (JDK 21 pinning scope, not
# generalized to JDK 24+).
#
# Unit 1 used the floating `21-jdk-jammy`/`21-jre-jammy` tags and documented
# the resolved patch (21.0.11+10-LTS) as if it were pinned — it wasn't: a
# floating tag can silently move to a newer patch (e.g. 21.0.12) between
# Docker builds, which would let the JDK patch drift mid-benchmark-campaign
# without anyone changing this file. Unit 2 pins the EXACT tag instead
# (verified to exist for linux/arm64/v8 via `docker manifest inspect` before
# adopting it — see version-compatibility.md section 6 for the digests).
FROM eclipse-temurin:21.0.11_10-jdk-jammy@sha256:55fb9bf738f5d9b4a6c01b39337e3070d3e27370dd3c478fd1d5d3cd2233c6d8 AS build
WORKDIR /workspace
COPY gradlew gradlew.bat build.gradle settings.gradle ./
COPY gradle ./gradle
COPY src ./src
RUN ./gradlew clean build -x test --no-daemon

FROM eclipse-temurin:21.0.11_10-jre-jammy@sha256:3097cbbebb7d490494a98aed2301f284b38f79eba158eef098c6fc8c8af11c23
WORKDIR /app
COPY --from=build /workspace/build/libs/gateway-mvc-java21-0.1.0.jar app.jar

ENV SERVER_PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz || exit 1

CMD ["java", "-jar", "app.jar"]
