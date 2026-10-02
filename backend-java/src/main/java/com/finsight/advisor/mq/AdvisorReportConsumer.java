package com.finsight.advisor.mq;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.advisor.entity.AdvisorReport;
import com.finsight.advisor.mapper.AdvisorReportMapper;
import com.finsight.chat.AgentClient;
import com.finsight.common.metrics.ObservabilityService;
import com.finsight.common.mq.ManualAckSupport;
import com.finsight.config.RabbitConfig;
import com.finsight.market.mapper.FinanceMapper;
import com.finsight.notify.NotificationService;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 作品说明：诊股报告消费者：取评分快照 + 近年指标 → Python LLM 生成 Markdown → 回写 + 站内信。
 * 失败由监听器自动重试，重试耗尽进死信并把报告标记 FAILED 留给用户重试。
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(name = "finsight.features.advisor-enabled", havingValue = "true")
public class AdvisorReportConsumer {

    private final AdvisorReportMapper reportMapper;
    private final FinanceMapper financeMapper;
    private final AgentClient agentClient;
    private final NotificationService notificationService;
    private final ObjectMapper objectMapper;
    private final ObservabilityService observabilityService;

    @RabbitListener(queues = RabbitConfig.ADVISOR_REPORT_QUEUE, concurrency = "1")
    public void onReportTask(AdvisorReportMessage message, Channel channel, Message amqpMessage) {
        AdvisorReport report = reportMapper.selectById(message.reportId());
        if (report == null || AdvisorReport.STATUS_READY.equals(report.getStatus())) {
            ManualAckSupport.ack(channel, amqpMessage);
            return;
        }

        long startedAt = System.currentTimeMillis();
        try {
            Map<String, Object> payload = new HashMap<>();
            payload.put("stock_code", report.getStockCode());
            payload.put("stock_name", report.getStockName());
            payload.put("risk_profile", report.getRiskProfile());
            payload.put("score", scorePayload(report));
            payload.put("metrics", metricsPayload(report.getStockCode()));

            String reportMd = agentClient.generateAdvisorReport(payload);

            report.setReportMd(reportMd);
            report.setStatus(AdvisorReport.STATUS_READY);
            reportMapper.updateById(report);

            notificationService.send(report.getUserId(), "advisor_report",
                    report.getStockName() + " AI 诊断报告已生成",
                    String.format("综合评分 %d（%s），点击查看完整报告。", report.getScore(), report.getRating()),
                    "/market/" + report.getStockCode());
            observabilityService.recordAdvisorReport(AdvisorReport.STATUS_READY, System.currentTimeMillis() - startedAt);
            log.info("[advisor] 报告生成完成 reportId={} stock={}", report.getId(), report.getStockCode());
        } catch (Exception e) {
            log.error("[advisor] 报告生成失败 reportId={}: {}", report.getId(), e.toString());
            markFailedIfLastAttempt(report, e);
            observabilityService.recordAdvisorReport(AdvisorReport.STATUS_FAILED, System.currentTimeMillis() - startedAt);
            throw new IllegalStateException("诊股报告生成失败", e); // 作品说明：触发监听器重试
        }
        ManualAckSupport.ack(channel, amqpMessage);
    }

    /**
     * 作品说明：标记失败状态（每次尝试都更新，最后一次重试失败后状态即为 FAILED；
     * 若后续重试成功会再覆盖为 READY，状态最终一致）。
     */
    private void markFailedIfLastAttempt(AdvisorReport report, Exception e) {
        try {
            report.setStatus(AdvisorReport.STATUS_FAILED);
            String reason = e.getMessage() == null ? "生成失败" : e.getMessage();
            report.setReportMd("生成失败：" + reason.substring(0, Math.min(reason.length(), 500)));
            reportMapper.updateById(report);
        } catch (Exception ignored) {
        }
    }

    private Map<String, Object> scorePayload(AdvisorReport report) throws Exception {
        List<Map<String, Object>> dimensions = report.getDimensions() == null
                ? List.of()
                : objectMapper.readValue(report.getDimensions(), new TypeReference<>() {
        });
        return Map.of(
                "total", report.getScore(),
                "rating", report.getRating(),
                "dimensions", dimensions);
    }

    /** 作品说明：近年年报指标快照（核心 + 资负 + 现金流，金额单位万元） */
    private Map<String, Object> metricsPayload(String stockCode) {
        return Map.of(
                "core_yearly", tail(financeMapper.coreSeries(stockCode), 4),
                "balance_yearly", tail(financeMapper.balanceSeries(stockCode), 4),
                "cashflow_yearly", tail(financeMapper.cashflowSeries(stockCode), 4),
                "amount_unit", "万元");
    }

    private static List<Map<String, Object>> tail(List<Map<String, Object>> rows, int n) {
        return rows.size() <= n ? rows : rows.subList(rows.size() - n, rows.size());
    }
}
