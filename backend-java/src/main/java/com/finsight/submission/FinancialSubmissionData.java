package com.finsight.submission;

import java.util.*;
import java.util.regex.Pattern;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.etl.entity.EtlTask;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;

/** Only public candidate evidence crosses the service boundary. */
public final class FinancialSubmissionData {
    private FinancialSubmissionData() { }
    private static final ObjectMapper JSON = new ObjectMapper();

    private static final Pattern LOCAL_PATH = Pattern.compile(
            "(?i)(?:(?<![\\p{L}\\p{N}_])[a-z]:[\\\\/]|\\\\\\\\)[^\\s\"'<>]*|(?<![\\p{L}\\p{N}_:/~.%-])/(?:[^\\s\"'<>]+)");
    private static final Pattern CREDENTIAL = Pattern.compile(
            "(?i)(?:(?:api[_-]?key|password|secret|token|authorization)\\s*[:=]\\s*\\S+|\\bsk-[a-z0-9_-]{8,})");

    public static String text(String value) {
        if (value == null) return null;
        return CREDENTIAL.matcher(LOCAL_PATH.matcher(value).replaceAll("[redacted]")).replaceAll("[redacted]");
    }

    public static String fileName(String value) {
        if (value == null) return "report.pdf";
        String name = value.replace('\\', '/');
        return name.substring(name.lastIndexOf('/') + 1).replaceAll("[\\p{Cntrl}]", "_");
    }

    public static String diagnostics(String value) {
        if (value == null) return null;
        try { return JSON.writeValueAsString(sanitize(JSON.readValue(value, Object.class))); }
        catch (Exception e) { return text(value); }
    }

    public static boolean recoverable(EtlTask task, ObjectMapper objectMapper) {
        if (Boolean.TRUE.equals(task.getRetryable())) return true;
        if (task.getStepsJson() == null) return task.getCandidateId() == null;
        try { return objectMapper.readTree(task.getStepsJson()).path("retryable_steps").size() > 0; }
        catch (Exception e) { return false; }
    }

    public static Map<String,Object> publicationReceipt(Map<String,Object> value) {
        Object version = value.get("version"); Object collection = value.get("collection");
        if (!(version instanceof String versionText) || !versionText.matches("[a-zA-Z0-9_.-]{1,255}")
                || !(collection instanceof String collectionText) || !collectionText.matches("[a-zA-Z0-9_.-]{1,255}")
                || !(value.get("reports") instanceof Number reports) || reports.longValue() < 1
                || !(value.get("facts") instanceof Number facts) || facts.longValue() < 0) {
            throw new BizException(ErrorCode.AGENT_UNAVAILABLE, "Publication receipt is incomplete");
        }
        return Map.of("version", version, "reports", value.get("reports"), "facts", value.get("facts"), "collection", collection);
    }

    public static Object sanitize(Object value) {
        if (value instanceof Map<?, ?> values) {
            Map<String,Object> result = new LinkedHashMap<>();
            values.forEach((key, entry) -> {
                String name = String.valueOf(key);
                String lower = name.toLowerCase(Locale.ROOT);
                if (!lower.contains("path") && !lower.contains("token") && !lower.contains("secret")
                        && !lower.contains("password") && !lower.contains("api_key") && !lower.contains("owner")
                        && !lower.contains("api-key") && !lower.contains("apikey") && !lower.contains("authorization")
                        && !Set.of("created_by", "createdby", "operator_id", "operatorid", "user_id", "userid").contains(lower)) {
                    result.put(name, sanitize(entry));
                }
            });
            return result;
        }
        if (value instanceof Collection<?> values) return values.stream().map(FinancialSubmissionData::sanitize).toList();
        return value instanceof String string ? text(string) : value;
    }
}
