# Build and runtime both pinned to JDK 8 (Eclipse Temurin) to match the
# real Java 8 legacy environment this Baseline reproduces — see
# docs/decisions/version-compatibility.md.

FROM eclipse-temurin:8-jdk-jammy AS build
WORKDIR /workspace
COPY gradlew gradlew.bat build.gradle settings.gradle ./
COPY gradle ./gradle
COPY src ./src
RUN ./gradlew clean build -x test --no-daemon

FROM eclipse-temurin:8-jre-jammy
WORKDIR /app
COPY --from=build /workspace/build/libs/gateway-mvc-executor-java8-0.1.0.jar app.jar

ENV SERVER_PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD wget -qO- http://localhost:8080/healthz || exit 1

CMD ["java", "-jar", "app.jar"]
