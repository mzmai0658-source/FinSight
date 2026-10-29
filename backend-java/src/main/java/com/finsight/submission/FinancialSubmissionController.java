package com.finsight.submission;

import com.finsight.common.api.ApiResponse;
import com.finsight.common.ratelimit.RateLimit;
import com.finsight.common.ratelimit.RateLimitKey;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.Resource;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import static com.finsight.submission.FinancialSubmissionDtos.*;

@RestController
@RequestMapping("/api/financial-submissions")
@RequiredArgsConstructor
public class FinancialSubmissionController {
    private final FinancialSubmissionService submissionService;

    @PostMapping
    @RateLimit(name = "financial-submit", keyBy = RateLimitKey.USER_IP, windowSeconds = 300, limit = 10,
            message = "上传过于频繁，请稍后再试")
    public ApiResponse<SubmissionView> submit(@RequestParam("file") MultipartFile file) {
        return ApiResponse.ok(submissionService.submit(file));
    }

    @GetMapping
    public ApiResponse<PageView> mine(@RequestParam(defaultValue = "1") int page,
                                     @RequestParam(defaultValue = "20") int size) {
        return ApiResponse.ok(submissionService.mine(page, size));
    }

    @GetMapping("/{id}")
    public ApiResponse<SubmissionView> detail(@PathVariable long id) {
        return ApiResponse.ok(submissionService.get(id));
    }

    @PostMapping("/{id}/retry")
    public ApiResponse<SubmissionView> retry(@PathVariable long id) {
        return ApiResponse.ok(submissionService.retry(id));
    }

    @GetMapping("/{id}/original")
    public ResponseEntity<Resource> original(@PathVariable long id) {
        return submissionService.original(id);
    }
}
