package com.finsight.submission;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.finsight.auth.LoginUser;
import com.finsight.chat.AgentClient;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import com.finsight.etl.EtlTaskService;
import com.finsight.etl.entity.EtlTask;
import com.finsight.etl.mapper.EtlTaskMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.Resource;
import org.springframework.core.io.FileSystemResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.function.Supplier;

import static com.finsight.submission.FinancialSubmissionDtos.*;

@Service
@RequiredArgsConstructor
public class FinancialSubmissionService {
    private final EtlTaskMapper taskMapper;
    private final EtlTaskService etlTaskService;
    private final AgentClient agentClient;
    private final ObjectMapper objectMapper;
    private final StringRedisTemplate redis;

    @Value("${finsight.etl.upload-dir}")
    private String uploadDir;

    private static final Set<String> REVIEW_STATUSES = Set.of("PROCESSING", "WAITING", "APPROVED", "REJECTED", "PUBLISHED");
    private static final Set<String> CANDIDATE_KEYS = Set.of("candidate_id", "status", "review_status", "source_sha256",
            "identity", "page_count", "steps", "facts", "excluded", "narrative_count", "index", "review", "quality");

    public SubmissionView submit(MultipartFile file) {
        return view(etlTaskService.submit(file, "financial", SecurityUtils.currentUserId()));
    }

    public PageView mine(int page, int size) {
        long owner = SecurityUtils.currentUserId();
        validatePage(page, size);
        return page(page, size, new LambdaQueryWrapper<EtlTask>().eq(EtlTask::getFileType, "financial")
                .eq(EtlTask::getCreatedBy, owner));
    }

    public SubmissionView get(long id) {
        return view(owned(id, false));
    }

    @Transactional
    public SubmissionView retry(long id) {
        EtlTask task = owned(id, true);
        if (!Set.of("FAILED", "PARTIAL").contains(task.getStatus())
                || Set.of("APPROVED", "REJECTED", "PUBLISHED").contains(Objects.toString(task.getReviewStatus(), ""))
                || !FinancialSubmissionData.recoverable(task, objectMapper)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Only recoverable processing failures can be retried");
        }
        return view(etlTaskService.retry(id));
    }

    public ResponseEntity<Resource> original(long id) {
        EtlTask task = owned(id, false);
        try {
            Path root = Path.of(uploadDir).toRealPath();
            Path original = Path.of(task.getFilePath()).toRealPath();
            if (!original.startsWith(root) || !Files.isRegularFile(original)) throw new java.io.IOException();
            try (var input = Files.newInputStream(original)) {
                if (!Arrays.equals(input.readNBytes(5), "%PDF-".getBytes(StandardCharsets.US_ASCII))) {
                    throw new java.io.IOException();
                }
            }
            return ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF).contentLength(Files.size(original))
                    .header(HttpHeaders.CACHE_CONTROL, "private, no-store")
                    .header("X-Content-Type-Options", "nosniff").body(new FileSystemResource(original));
        } catch (Exception e) {
            throw new BizException(ErrorCode.NOT_FOUND, "Original PDF is unavailable");
        }
    }

    public PageView adminPage(int page, int size, String status) {
        requireAdmin(); validatePage(page, size);
        if (status != null && !status.isBlank() && !REVIEW_STATUSES.contains(status)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Unknown review status");
        }
        return page(page, size, new LambdaQueryWrapper<EtlTask>().eq(EtlTask::getFileType, "financial")
                .eq(status != null && !status.isBlank(), EtlTask::getReviewStatus, status));
    }

    @SuppressWarnings("unchecked")
    public Map<String,Object> candidate(long id) {
        requireAdmin(); EtlTask task = financial(id, false);
        requireCandidate(task);
        Map<String,Object> result = callPython(() -> agentClient.financialCandidate(task.getCandidateId()));
        verifyCandidateReceipt(task, result);
        Map<String,Object> allowed = new LinkedHashMap<>();
        result.forEach((key, value) -> { if (CANDIDATE_KEYS.contains(key)) allowed.put(key, value); });
        return (Map<String,Object>) FinancialSubmissionData.sanitize(allowed);
    }

    @Transactional
    public SubmissionView review(long id, ReviewRequest request) {
        long operator = requireAdmin().userId();
        Map<String,Object> payload = reviewPayload(request, operator);
        lockOperations();
        EtlTask task = financial(id, true); requireCandidate(task);
        if (Set.of("PENDING", "RUNNING").contains(task.getStatus()) || "PUBLISHED".equals(task.getReviewStatus())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "The submission cannot be reviewed in its current state");
        }
        if ("approve".equals(request.decision()) && !Set.of("SUCCESS", "PARTIAL").contains(task.getStatus())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Processing must complete before approval");
        }
        Map<String,Object> result = callPython(() -> agentClient.reviewFinancialCandidate(task.getCandidateId(), payload));
        verifyCandidateReceipt(task, result);
        String accepted = "approve".equals(request.decision()) ? "APPROVED" : "REJECTED";
        if (!accepted.equals(result.get("review_status"))) throw new BizException(ErrorCode.AGENT_UNAVAILABLE, "Review was not confirmed");
        Map<?,?> identity = result.get("identity") instanceof Map<?,?> values ? values : Map.of();
        String stockCode = string(identity.get("stock_code"));
        String company = string(identity.get("company"));
        Integer year = identity.get("year") instanceof Number number ? number.intValue() : null;
        String period = string(identity.get("period"));
        if ("APPROVED".equals(accepted) && !validIdentity(new Identity(stockCode, company, year, period, List.of()))) {
            throw new BizException(ErrorCode.AGENT_UNAVAILABLE, "Review identity was not confirmed");
        }
        var update = new LambdaUpdateWrapper<EtlTask>().eq(EtlTask::getFileType, "financial")
                .eq(EtlTask::getCandidateId, task.getCandidateId()).notIn(EtlTask::getStatus, "PENDING", "RUNNING")
                .ne(EtlTask::getReviewStatus, "PUBLISHED").set(EtlTask::getReviewStatus, accepted)
                .set(EtlTask::getStockCode, stockCode == null ? "" : stockCode).set(EtlTask::getCompany, company)
                .set(EtlTask::getReportYear, year).set(EtlTask::getReportPeriod, period);
        requireUpdated(taskMapper.update(null, update));
        task.setReviewStatus(accepted); task.setStockCode(stockCode); task.setCompany(company);
        task.setReportYear(year); task.setReportPeriod(period);
        return view(task);
    }

    @Transactional
    public Map<String,Object> publish(PublishRequest request) {
        long operator = requireAdmin().userId();
        if (request == null || request.ids() == null || request.ids().isEmpty() || request.ids().size() > 200
                || request.ids().stream().anyMatch(id -> id == null || id < 1)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Select between 1 and 200 submissions");
        }
        lockOperations();
        List<EtlTask> selected = request.ids().stream().distinct().sorted().map(id -> financial(id, true)).toList();
        selected.forEach(FinancialSubmissionService::requireCandidate);
        List<String> candidates = selected.stream().map(EtlTask::getCandidateId).distinct().sorted().toList();
        if (selected.stream().allMatch(task -> "PUBLISHED".equals(task.getReviewStatus()))) {
            return replay(selected, candidates);
        }
        if (selected.stream().anyMatch(task -> !"APPROVED".equals(task.getReviewStatus())
                || !Set.of("SUCCESS", "PARTIAL").contains(task.getStatus()))) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Each selected submission must be approved before publication");
        }
        Map<String,Object> receipt = publicationReceipt(callPython(() -> agentClient.publishFinancialCandidates(candidates, operator)));
        String version = (String) receipt.get("version");
        String stored = encode(Map.of("receipt", receipt, "candidate_ids", candidates));
        for (String candidate : candidates) {
            requireUpdated(taskMapper.update(null, new LambdaUpdateWrapper<EtlTask>()
                    .eq(EtlTask::getFileType, "financial").eq(EtlTask::getCandidateId, candidate)
                    .notIn(EtlTask::getStatus, "PENDING", "RUNNING")
                    .set(EtlTask::getReviewStatus, "PUBLISHED").set(EtlTask::getPublishedVersion, version)
                    .set(EtlTask::getPublicationReceiptJson, stored)));
        }
        selected.forEach(task -> { task.setReviewStatus("PUBLISHED"); task.setPublishedVersion(version); task.setPublicationReceiptJson(stored); });
        evictMarketCaches();
        return receipt;
    }

    private PageView page(int page, int size, LambdaQueryWrapper<EtlTask> filter) {
        Page<EtlTask> result = taskMapper.selectPage(Page.of(page, size), filter.orderByDesc(EtlTask::getId));
        return new PageView(result.getTotal(), page, result.getRecords().stream().map(FinancialSubmissionService::view).toList());
    }

    private EtlTask owned(long id, boolean lock) {
        LoginUser user = SecurityUtils.currentUser();
        EtlTask task = financial(id, lock);
        if (!user.isAdmin() && !Objects.equals(task.getCreatedBy(), user.userId())) throw new BizException(ErrorCode.FORBIDDEN);
        return task;
    }

    private EtlTask financial(long id, boolean lock) {
        if (id < 1) throw new BizException(ErrorCode.BAD_REQUEST);
        EtlTask task = lock ? taskMapper.lockFinancialSubmission(id) : taskMapper.selectById(id);
        if (task == null || !"financial".equals(task.getFileType())) throw new BizException(ErrorCode.NOT_FOUND, "Financial submission not found");
        return task;
    }

    private static LoginUser requireAdmin() {
        LoginUser user = SecurityUtils.currentUser();
        if (!user.isAdmin()) throw new BizException(ErrorCode.FORBIDDEN);
        return user;
    }

    private void lockOperations() {
        if (taskMapper.lockSubmissionOperations() == null) throw new IllegalStateException("Submission operation lock is unavailable");
    }

    private static void validatePage(int page, int size) {
        if (page < 1 || size < 1 || size > 50) throw new BizException(ErrorCode.BAD_REQUEST);
    }

    private static void requireCandidate(EtlTask task) {
        if (task.getCandidateId() == null || !task.getCandidateId().matches("[a-f0-9]{64}")
                || !task.getCandidateId().equals(task.getSourceSha256())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "The submission has no processed candidate");
        }
    }

    private static void verifyCandidateReceipt(EtlTask task, Map<String,Object> result) {
        if (!task.getCandidateId().equals(result.get("candidate_id"))
                || !task.getSourceSha256().equals(result.get("source_sha256"))) {
            throw new BizException(ErrorCode.AGENT_UNAVAILABLE, "Candidate receipt did not match the submission");
        }
    }

    private static Map<String,Object> reviewPayload(ReviewRequest request, long operator) {
        if (request == null || !Set.of("approve", "reject").contains(Objects.toString(request.decision(), ""))
                || (request.reason() != null && request.reason().length() > 2000)
                || ("reject".equals(request.decision()) && (request.reason() == null || request.reason().isBlank()))
                || ("approve".equals(request.decision()) && !validIdentity(request.identity()))) {
            throw new BizException(ErrorCode.BAD_REQUEST, "Approval requires a confirmed report identity; rejection requires a reason");
        }
        Map<String,Object> payload = new LinkedHashMap<>();
        payload.put("decision", request.decision()); payload.put("reason", request.reason() == null ? "" : request.reason().trim());
        payload.put("operator_id", operator);
        if (request.identity() != null) {
            Identity identity = request.identity();
            if (!validIdentity(identity)) throw new BizException(ErrorCode.BAD_REQUEST, "Invalid report identity");
            payload.put("identity", Map.of("stock_code", identity.stockCode(), "company", identity.company().trim(),
                    "year", identity.year(), "period", identity.period(), "aliases", identity.aliases() == null ? List.of() : identity.aliases()));
        }
        return payload;
    }

    private static boolean validIdentity(Identity identity) {
        return identity != null && identity.stockCode() != null && identity.stockCode().matches("\\d{6}")
                && identity.company() != null && !identity.company().isBlank() && identity.company().length() <= 255
                && identity.year() != null && identity.year() >= 1900 && identity.year() <= 2200
                && Set.of("FY", "HY", "Q1", "Q3").contains(Objects.toString(identity.period(), ""))
                && (identity.aliases() == null || identity.aliases().size() <= 50 && identity.aliases().stream()
                    .allMatch(alias -> alias != null && !alias.isBlank() && alias.length() <= 255));
    }

    private static Map<String,Object> callPython(Supplier<Map<String,Object>> action) {
        try {
            Map<String,Object> result = action.get();
            if (result == null) throw new IllegalStateException();
            return result;
        } catch (WebClientResponseException e) {
            if (e.getStatusCode().value() == 404) throw new BizException(ErrorCode.NOT_FOUND, "Candidate is unavailable");
            if (e.getStatusCode().value() == 400 || e.getStatusCode().value() == 422) {
                throw new BizException(ErrorCode.BAD_REQUEST, "Candidate validation failed; check its identity and quality details");
            }
            if (e.getStatusCode().value() == 409) throw new BizException(ErrorCode.CONFLICT, "Candidate state changed or publication requirements were not met");
            throw new BizException(ErrorCode.AGENT_UNAVAILABLE);
        } catch (Exception e) {
            throw new BizException(ErrorCode.AGENT_UNAVAILABLE, "Financial processing service did not confirm the operation");
        }
    }

    private static Map<String,Object> publicationReceipt(Map<String,Object> value) {
        return FinancialSubmissionData.publicationReceipt(value);
    }

    private Map<String,Object> replay(List<EtlTask> selected, List<String> candidates) {
        try {
            Map<String,Object> stored = objectMapper.readValue(selected.get(0).getPublicationReceiptJson(), new TypeReference<>() {});
            if (!candidates.equals(stored.get("candidate_ids")) || selected.stream().anyMatch(task ->
                    !Objects.equals(task.getPublicationReceiptJson(), selected.get(0).getPublicationReceiptJson()))) {
                throw new IllegalArgumentException();
            }
            return publicationReceipt(objectMapper.convertValue(stored.get("receipt"), new TypeReference<Map<String,Object>>() {}));
        } catch (Exception e) {
            throw new BizException(ErrorCode.CONFLICT, "These submissions were already published in a different batch");
        }
    }

    private String encode(Object value) {
        try { return objectMapper.writeValueAsString(value); }
        catch (Exception e) { throw new IllegalStateException("Cannot save publication receipt", e); }
    }

    private static void requireUpdated(int count) {
        if (count < 1) throw new BizException(ErrorCode.CONFLICT, "Submission changed before the operation was saved");
    }

    private static String string(Object value) { return value instanceof String text && !text.isBlank() ? text : null; }

    public static SubmissionView view(EtlTask task) {
        boolean candidate = task.getCandidateId() != null;
        return new SubmissionView(task.getId(), task.getTaskUid(), FinancialSubmissionData.fileName(task.getFileName()),
                task.getStatus(), task.getReviewStatus(), task.getCandidateId(), candidate ? task.getSourceSha256() : null,
                candidate ? string(task.getStockCode()) : null, candidate ? FinancialSubmissionData.text(task.getCompany()) : null,
                candidate ? task.getReportYear() : null, candidate ? task.getReportPeriod() : null,
                FinancialSubmissionData.text(task.getMessage()), task.getCreatedAt(), task.getStartedAt(), task.getFinishedAt(), task.getPublishedVersion());
    }

    private void evictMarketCaches() {
        try {
            for (String pattern : List.of("market:companies*", "market:detail:*", "market:series:*")) {
                Set<String> keys = redis.keys(pattern);
                if (keys != null && !keys.isEmpty()) redis.delete(keys);
            }
            redis.delete("market:summary");
        } catch (Exception ignored) { /* Cache TTL remains the fallback after a committed Python publication. */ }
    }
}
