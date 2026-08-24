package com.llmconcurrencylab.gatewaymvc.deadline;

import java.util.concurrent.TimeoutException;

/**
 * Dedicated type (rather than a bare {@link TimeoutException}) so {@code ChatController}'s
 * {@code onError} handler can distinguish "our own absolute-deadline operator fired" from any
 * other timeout-shaped exception Reactor Netty might produce (e.g. its own response timeout) by
 * type, not by message-string matching.
 */
public final class AbsoluteDeadlineExceededException extends TimeoutException {

    public AbsoluteDeadlineExceededException() {
        super("absolute-deadline-exceeded");
    }
}
