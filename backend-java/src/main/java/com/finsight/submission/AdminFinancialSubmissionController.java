package com.finsight.submission;

import com.finsight.common.api.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

import static com.finsight.submission.FinancialSubmissionDtos.*;

@RestController
@RequestMapping("/api/admin/financial-submissions")
@PreAuthorize("hasRole('ADMIN')")
@RequiredArgsConstructor
public class AdminFinancialSubmissionController {
    private final FinancialSubmissionService submissionService;

    @GetMapping
    public ApiResponse<PageView> list(@RequestParam(defaultValue = "1") int page,
                                     @RequestParam(defaultValue = "20") int size,
                                     @RequestParam(required = false) String reviewStatus) {
        return ApiResponse.ok(submissionService.adminPage(page, size, reviewStatus));
    }

    @GetMapping("/{id}/candidate")
    public ApiResponse<Map<String,Object>> candidate(@PathVariable long id) {
        return ApiResponse.ok(submissionService.candidate(id));
    }

    @PostMapping("/{id}/review")
    public ApiResponse<SubmissionView> review(@PathVariable long id, @RequestBody ReviewRequest request) {
        return ApiResponse.ok(submissionService.review(id, request));
    }

    @PostMapping("/publish")
    public ApiResponse<Map<String,Object>> publish(@RequestBody PublishRequest request) {
        return ApiResponse.ok(submissionService.publish(request));
    }
}
