import asyncio
import logging
import os
import random
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import StreamingResponse

LOG_LEVEL = os.environ.get("MOCK_LLM_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mock-llm")


def log(request_id: str, message: str) -> None:
    logger.info(f"[{request_id}] {message}")


# Global capacity controls (Section 20: FastAPI Bottleneck Simulation).
# 0 means unlimited — Phase 1 Baseline runs with this unset so the Java Gateway,
# not the Mock LLM, is the object under test.
MAX_CONCURRENT_PROCESSING = int(os.environ.get("MOCK_LLM_MAX_CONCURRENT_PROCESSING", "0"))
MAX_WAITING = int(os.environ.get("MOCK_LLM_MAX_WAITING", "0"))

_processing_semaphore: Optional[asyncio.Semaphore] = (
    asyncio.Semaphore(MAX_CONCURRENT_PROCESSING) if MAX_CONCURRENT_PROCESSING > 0 else None
)
_waiting_count = 0

app = FastAPI(title="mock-llm-fastapi")

active_requests = Gauge("mockllm_active_requests", "Requests currently accepted (waiting + processing)")
waiting_requests = Gauge("mockllm_waiting_requests", "Requests waiting for a processing slot")
current_concurrency = Gauge("mockllm_current_concurrency", "Requests currently being processed")
completed_requests = Counter("mockllm_completed_requests_total", "Requests completed with a final event")
failed_requests = Counter("mockllm_failed_requests_total", "Requests that ended in failure", ["reason"])
cancelled_requests = Counter("mockllm_cancelled_requests_total", "Requests cancelled by the client mid-stream")
first_chunk_latency = Histogram("mockllm_first_chunk_latency_seconds", "Time from request start to first chunk sent")
stream_duration = Histogram("mockllm_stream_duration_seconds", "Time from request start to stream termination")
bytes_streamed = Counter("mockllm_bytes_streamed_total", "Cumulative bytes sent in delta event payloads")


class StreamRequest(BaseModel):
    requestId: Optional[str] = None
    firstChunkDelayMs: int = Field(default=200, ge=0)
    chunkIntervalMs: int = Field(default=100, ge=0)
    chunkCount: int = Field(default=10, ge=1)
    chunkSizeBytes: int = Field(default=64, ge=1)
    failureRate: float = Field(default=0.0, ge=0.0, le=1.0)
    midStreamFailureRate: float = Field(default=0.0, ge=0.0, le=1.0)
    failAtChunk: Optional[int] = Field(default=None, ge=1)
    forcedDisconnectAfterChunk: Optional[int] = Field(default=None, ge=1)
    maxDurationMs: int = Field(default=30000, ge=0)
    # One-off diagnostic knob (not part of the normal simulation surface):
    # replaces the single inter-chunk gap after `stallAfterChunk` with
    # `stallMs` instead of the usual chunkIntervalMs, so a caller can send a
    # few chunks normally and then stall far longer than any Gateway timeout —
    # used by Unit 5's absolute-deadline-vs-blocking-readLine() verification
    # (docs/test-results/phase1/absolute-deadline-blocking-read.md).
    stallAfterChunk: Optional[int] = Field(default=None, ge=1)
    stallMs: int = Field(default=0, ge=0)


def sse_event(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def generate_stream(req: StreamRequest, request: Request, request_id: str):
    global _waiting_count
    start = time.monotonic()
    active_requests.inc()
    acquired = False
    # `outcome` is the single source of truth for the terminal state, written to
    # exactly once, and — critically — only ever written to *after* a yield has
    # actually been handed back to a resumed generator (see comment below on why
    # that ordering is what makes completed/failed/cancelled mutually exclusive).
    outcome: Optional[str] = None
    try:
        if _processing_semaphore is not None:
            if MAX_WAITING > 0 and _waiting_count >= MAX_WAITING:
                log(request_id, "rejected: waiting queue full")
                yield sse_event("error", '{"status":"FAILED","reason":"waiting_queue_full"}')
                outcome = "waiting_queue_full"
                return
            _waiting_count += 1
            waiting_requests.set(_waiting_count)
            try:
                await _processing_semaphore.acquire()
            finally:
                _waiting_count -= 1
                waiting_requests.set(_waiting_count)
            acquired = True
            current_concurrency.inc()

        if random.random() < req.failureRate:
            await asyncio.sleep(req.firstChunkDelayMs / 1000)
            log(request_id, "immediate failure (failureRate triggered)")
            yield sse_event("error", '{"status":"FAILED","reason":"immediate_failure"}')
            outcome = "immediate_failure"
            return

        if await request.is_disconnected():
            log(request_id, "client disconnected before first chunk")
            outcome = "cancelled"
            return

        await asyncio.sleep(req.firstChunkDelayMs / 1000)
        first_chunk_latency.observe(time.monotonic() - start)

        mid_stream_fail_at = req.failAtChunk
        if mid_stream_fail_at is None and random.random() < req.midStreamFailureRate and req.chunkCount > 1:
            mid_stream_fail_at = random.randint(1, req.chunkCount - 1)

        payload_text = "x" * req.chunkSizeBytes

        for sequence in range(1, req.chunkCount + 1):
            if (time.monotonic() - start) * 1000 > req.maxDurationMs:
                log(request_id, f"max duration exceeded at chunk {sequence}")
                yield sse_event("error", '{"status":"FAILED","reason":"max_duration_exceeded"}')
                outcome = "max_duration_exceeded"
                return

            if await request.is_disconnected():
                log(request_id, f"client disconnected at chunk {sequence}")
                outcome = "cancelled"
                return

            if req.forcedDisconnectAfterChunk is not None and sequence > req.forcedDisconnectAfterChunk:
                log(request_id, f"forced disconnect after chunk {req.forcedDisconnectAfterChunk}")
                # Intentionally leaves outcome=None: from the client's point of view this
                # is indistinguishable from an uncontrolled network cut, which is exactly
                # the failure mode this knob simulates. Recording it as "cancelled" would
                # misrepresent it as client-initiated.
                return

            data = f'{{"sequence":{sequence},"text":"{payload_text}"}}'
            yield sse_event("delta", data)
            bytes_streamed.inc(len(data))

            if mid_stream_fail_at is not None and sequence == mid_stream_fail_at:
                log(request_id, f"mid-stream failure at chunk {sequence}")
                yield sse_event("error", '{"status":"FAILED","reason":"mid_stream_failure"}')
                outcome = "mid_stream_failure"
                return

            if sequence < req.chunkCount:
                if req.stallAfterChunk is not None and sequence == req.stallAfterChunk and req.stallMs > 0:
                    log(request_id, f"stalling {req.stallMs}ms after chunk {sequence} (diagnostic knob)")
                    await asyncio.sleep(req.stallMs / 1000)
                else:
                    await asyncio.sleep(req.chunkIntervalMs / 1000)

        yield sse_event("final", '{"status":"COMPLETED"}')
        # This line only runs if the generator is resumed *after* the final yield above —
        # which, per Starlette's `async for chunk in body_iterator: await send(chunk)` loop,
        # only happens once that send() has actually succeeded. If the client disconnected
        # exactly as the final chunk was being written, send() raises, the generator is
        # never resumed past the yield, and it is torn down via GeneratorExit instead — so
        # this line and the except-clause below are structurally exclusive, not just by
        # convention. That's what keeps completed_requests and cancelled_requests disjoint
        # even when the disconnect races the very last event.
        outcome = "completed"
        log(request_id, "completed")
    except (asyncio.CancelledError, GeneratorExit):
        # ASGI servers typically close a StreamingResponse generator directly
        # (GeneratorExit) the moment a write fails, faster than our own
        # is_disconnected() poll above ever gets a chance to run. This is the
        # primary path that actually catches most real client disconnects,
        # including one racing the final event (see comment above).
        if outcome is None:
            outcome = "cancelled"
            log(request_id, "client disconnected (stream closed by server)")
        raise
    finally:
        stream_duration.observe(time.monotonic() - start)
        if outcome == "completed":
            completed_requests.inc()
        elif outcome == "cancelled":
            cancelled_requests.inc()
        elif outcome is not None:
            failed_requests.labels(reason=outcome).inc()
        if acquired:
            current_concurrency.dec()
            _processing_semaphore.release()
        active_requests.dec()


@app.post("/mock/stream")
async def mock_stream(req: StreamRequest, request: Request):
    request_id = req.requestId or str(uuid.uuid4())
    log(request_id, f"stream requested: chunkCount={req.chunkCount} firstChunkDelayMs={req.firstChunkDelayMs}")
    return StreamingResponse(
        generate_stream(req, request, request_id),
        media_type="text/event-stream",
        headers={"X-Request-Id": request_id, "Cache-Control": "no-cache"},
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
