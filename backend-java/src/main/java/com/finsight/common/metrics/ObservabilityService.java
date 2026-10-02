package com.finsight.common.metrics;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;

/** 作品说明：记录主要异步及流式工作流程的业务指标。 */
@Service
public class ObservabilityService {

    private final MeterRegistry registry;
    private final AtomicInteger activeSse = new AtomicInteger(0);

    public ObservabilityService(MeterRegistry registry) {
        this.registry = registry;
        Gauge.builder("finsight.chat.sse.active", activeSse, AtomicInteger::get)
                .register(registry);
    }

    public void recordChatTurn(String status, long durationMs) {
        record("finsight.chat.turns", "finsight.chat.turn.duration", status, durationMs);
    }

    public void recordEtlTask(String status, long durationMs) {
        record("finsight.etl.tasks", "finsight.etl.task.duration", status, durationMs);
    }

    public void recordAdvisorReport(String status, long durationMs) {
        record("finsight.advisor.reports", "finsight.advisor.report.duration", status, durationMs);
    }

    public void incrementActiveSse() {
        activeSse.incrementAndGet();
    }

    public void decrementActiveSse() {
        activeSse.updateAndGet(value -> Math.max(value - 1, 0));
    }

    public void recordMarketCacheHit(String cache) {
        Counter.builder("finsight.cache.market.hit")
                .tag("cache", normalizeLabel(cache))
                .register(registry)
                .increment();
    }

    public void recordMarketCacheMiss(String cache) {
        Counter.builder("finsight.cache.market.miss")
                .tag("cache", normalizeLabel(cache))
                .register(registry)
                .increment();
    }

    public void recordMqEtl(String status) {
        Counter.builder("finsight.mq.etl")
                .tag("status", normalizeStatus(status))
                .register(registry)
                .increment();
    }

    private void record(String counterName, String timerName, String status, long durationMs) {
        String normalizedStatus = normalizeStatus(status);
        Counter.builder(counterName)
                .tag("status", normalizedStatus)
                .register(registry)
                .increment();
        Timer.builder(timerName)
                .tag("status", normalizedStatus)
                .publishPercentileHistogram()
                .register(registry)
                .record(Duration.ofMillis(Math.max(durationMs, 0L)));
    }

    private String normalizeStatus(String status) {
        return normalizeLabel(status);
    }

    private String normalizeLabel(String status) {
        if (status == null || status.isBlank()) {
            return "unknown";
        }
        return status.strip().toLowerCase();
    }
}
