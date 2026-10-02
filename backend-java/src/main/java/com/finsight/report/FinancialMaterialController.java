package com.finsight.report;

import com.finsight.common.api.ApiResponse;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import lombok.RequiredArgsConstructor;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import java.time.Duration;
import java.util.Map;

/** 作品说明：鉴权后访问只读问答已使用的现用财务快照。 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/materials/financial")
public class FinancialMaterialController {
    private final WebClient agentWebClient;

    @GetMapping
    public ApiResponse<Map<String,Object>> list(@RequestParam(defaultValue="1") int page,
            @RequestParam(defaultValue="12") int size, @RequestParam(defaultValue="") String keyword) {
        SecurityUtils.currentUserId();
        if(page<1 || size<1 || size>50 || keyword.length()>120) throw new BizException(ErrorCode.BAD_REQUEST);
        Map<String,Object> result=agentWebClient.get().uri(b -> b.path("/internal/materials/financial")
                .queryParam("page",page).queryParam("size",size).queryParam("keyword",keyword).build())
                .retrieve().bodyToMono(new ParameterizedTypeReference<Map<String,Object>>(){}).block(Duration.ofSeconds(20));
        if(result==null) throw new BizException(ErrorCode.AGENT_UNAVAILABLE);
        return ApiResponse.ok(result);
    }

    @GetMapping("/{code}/{year}/{period}/file")
    public ResponseEntity<byte[]> download(@PathVariable String code, @PathVariable int year, @PathVariable String period) {
        return file(code, year, period);
    }

    @GetMapping("/{id}/pages")
    public ApiResponse<Map<String,Object>> pages(@PathVariable long id, @RequestParam(defaultValue="1") int page) {
        SecurityUtils.currentUserId();
        if (id<1 || page<1) throw new BizException(ErrorCode.BAD_REQUEST);
        try {
            var result=agentWebClient.get().uri(b -> b.path("/internal/materials/financial/{id}/pages")
                    .queryParam("page",page).build(id)).retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String,Object>>(){}).block(Duration.ofSeconds(20));
            if(result==null) throw new BizException(ErrorCode.AGENT_UNAVAILABLE);
            return ApiResponse.ok(result);
        } catch(WebClientResponseException.NotFound e) {
            throw new BizException(ErrorCode.NOT_FOUND,"该财报正文或页码暂不可用");
        }
    }

    ResponseEntity<byte[]> file(String code, int year, String period) {
        SecurityUtils.currentUserId();
        if(!code.matches("\\d{6}") || year<1900 || year>2200 || !period.matches("FY|HY|Q1|Q3")) throw new BizException(ErrorCode.NOT_FOUND);
        ResponseEntity<byte[]> response;
        try {
            response=agentWebClient.get().uri("/internal/materials/financial/{code}/{year}/{period}/file",code,year,period)
                    .retrieve().toEntity(byte[].class).block(Duration.ofSeconds(30));
        } catch(WebClientResponseException.NotFound e) {
            throw new BizException(ErrorCode.NOT_FOUND,"原件缺失或版本已变化，请重新导入");
        }
        if(response==null || response.getBody()==null) throw new BizException(ErrorCode.AGENT_UNAVAILABLE);
        // 作品说明：当前 117 份原件在 WebClient 的 8 MiB 缓冲上限内。
        // 作品说明：在 MVC 提交 PDF 响应头之前，先完成下载与内容校验。
        // 作品说明：响应式缓冲区不直接写入 Servlet 流，避免两类响应生命周期混用。
        byte[] body=response.getBody();
        var type=response.getHeaders().getContentType();
        long declaredLength=response.getHeaders().getContentLength();
        boolean signature=body.length>=5 && body[0]=='%' && body[1]=='P' && body[2]=='D' && body[3]=='F' && body[4]=='-';
        if(type==null || !MediaType.APPLICATION_PDF.isCompatibleWith(type) || !signature ||
                (declaredLength>=0 && declaredLength!=body.length)) throw new BizException(ErrorCode.AGENT_UNAVAILABLE,"财报原件传输未完整通过检查，请重试");
        var builder=ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF)
                .header(HttpHeaders.CACHE_CONTROL,"private, no-store")
                .header("X-Content-Type-Options","nosniff");
        return builder.contentLength(body.length).body(body);
    }
}
