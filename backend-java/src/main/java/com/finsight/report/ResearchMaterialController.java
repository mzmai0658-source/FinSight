package com.finsight.report;

import com.finsight.common.api.ApiResponse;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import lombok.RequiredArgsConstructor;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import java.time.Duration;
import java.util.Map;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/materials/research")
public class ResearchMaterialController {
    private final WebClient agentWebClient;

    @GetMapping("/{id}/pages")
    public ApiResponse<Map<String,Object>> pages(@PathVariable long id, @RequestParam(defaultValue="1") int page) {
        SecurityUtils.currentUserId();
        if (id < 1 || page < 1) throw new BizException(ErrorCode.BAD_REQUEST);
        try {
            var result = agentWebClient.get().uri(b -> b.path("/internal/materials/research/{id}/pages")
                    .queryParam("page", page).build(id)).retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String,Object>>() {}).block(Duration.ofSeconds(20));
            if (result == null) throw new BizException(ErrorCode.AGENT_UNAVAILABLE);
            return ApiResponse.ok(result);
        } catch (WebClientResponseException.NotFound e) {
            throw new BizException(ErrorCode.NOT_FOUND, "该研报正文或页码暂不可用");
        }
    }
}
