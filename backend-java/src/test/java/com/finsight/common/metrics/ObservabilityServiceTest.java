package com.finsight.common.metrics;

import io.micrometer.core.instrument.Timer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

class ObservabilityServiceTest {

    @Test
    void recordsTaggedCountersAndTimers() {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        ObservabilityService service = new ObservabilityService(registry);

        service.recordChatTurn("ok", 125);
        service.recordEtlTask("success", 2500);
        service.recordAdvisorReport("failed", 800);
        service.incrementActiveSse();
        service.recordMarketCacheHit("summary");
        service.recordMarketCacheMiss("detail");
        service.recordMqEtl("success");

        assertThat(registry.find("finsight.chat.turns").tag("status", "ok").counter().count())
                .isEqualTo(1.0);
        Timer chatTimer = registry.find("finsight.chat.turn.duration").tag("status", "ok").timer();
        assertThat(chatTimer.count()).isEqualTo(1);
        assertThat(chatTimer.totalTime(TimeUnit.MILLISECONDS)).isEqualTo(125.0);

        assertThat(registry.find("finsight.etl.tasks").tag("status", "success").counter().count())
                .isEqualTo(1.0);
        assertThat(registry.find("finsight.advisor.reports").tag("status", "failed").counter().count())
                .isEqualTo(1.0);
        assertThat(registry.find("finsight.chat.sse.active").gauge().value()).isEqualTo(1.0);
        service.decrementActiveSse();
        assertThat(registry.find("finsight.chat.sse.active").gauge().value()).isEqualTo(0.0);
        assertThat(registry.find("finsight.cache.market.hit").tag("cache", "summary").counter().count())
                .isEqualTo(1.0);
        assertThat(registry.find("finsight.cache.market.miss").tag("cache", "detail").counter().count())
                .isEqualTo(1.0);
        assertThat(registry.find("finsight.mq.etl").tag("status", "success").counter().count())
                .isEqualTo(1.0);
    }
}
