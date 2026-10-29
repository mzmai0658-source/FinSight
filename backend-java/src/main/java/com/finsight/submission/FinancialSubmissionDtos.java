package com.finsight.submission;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.LocalDateTime;
import java.util.List;

public final class FinancialSubmissionDtos {
    private FinancialSubmissionDtos() { }

    public record SubmissionView(Long id, String taskUid, String fileName, String status, String reviewStatus,
            String candidateId, String sourceSha256, String stockCode, String company, Integer reportYear,
            String reportPeriod, String message, LocalDateTime createdAt, LocalDateTime startedAt,
            LocalDateTime finishedAt, String publishedVersion) { }

    public record PageView(long total, long page, List<SubmissionView> records) { }

    public record Identity(@JsonProperty("stock_code") String stockCode, String company, Integer year,
                           String period, List<String> aliases) { }

    public record ReviewRequest(String decision, String reason, Identity identity) { }

    public record PublishRequest(List<Long> ids) { }
}
