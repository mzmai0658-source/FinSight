package com.finsight.chat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.finsight.chat.mapper.ChatMessageMapper;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.core.io.buffer.DataBufferUtils;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;
import reactor.core.publisher.Flux;

import java.time.Duration;
import java.util.List;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;

/** 作品说明：只允许读取当前用户已保存回答引用的登记资产。 */
@RestController
@RequiredArgsConstructor
public class AssetController {
    private final ChatMessageMapper messageMapper;
    private final ObjectMapper objectMapper;
    private final WebClient agentWebClient;

    @GetMapping("/api/assets/{assetId}")
    public void download(@PathVariable String assetId, HttpServletResponse servletResponse) throws IOException {
        var result = asset(assetId);
        result.getHeaders().forEach((name, values) -> values.forEach(value -> servletResponse.addHeader(name, value)));
        servletResponse.flushBuffer();
        result.getBody().writeTo(servletResponse.getOutputStream());
        servletResponse.flushBuffer();
    }

    ResponseEntity<StreamingResponseBody> asset(String assetId) {
        requireAccess(SecurityUtils.currentUserId(), assetId);
        ResponseEntity<Flux<DataBuffer>> upstream;
        try {
            upstream = agentWebClient.get().uri("/internal/assets/{assetId}", assetId)
                    .retrieve().toEntityFlux(DataBuffer.class).block(Duration.ofSeconds(15));
        } catch (WebClientResponseException.NotFound e) {
            throw new BizException(ErrorCode.NOT_FOUND, "原文资产已不存在，请重新检索");
        }
        if (upstream == null || upstream.getBody() == null) {
            throw new BizException(ErrorCode.AGENT_UNAVAILABLE);
        }
        MediaType type = upstream.getHeaders().getContentType();
        Flux<DataBuffer> body = upstream.getBody();
        StreamingResponseBody stream = output -> DataBufferUtils.write(body, output)
                .doOnNext(DataBufferUtils::release)
                .doOnDiscard(DataBuffer.class, DataBufferUtils::release)
                .blockLast(Duration.ofMinutes(2));
        var builder = ResponseEntity.ok()
                .contentType(type == null ? MediaType.APPLICATION_OCTET_STREAM : type)
                .header(HttpHeaders.CACHE_CONTROL, "private, no-store")
                .header("X-Content-Type-Options", "nosniff")
                .header("Content-Security-Policy", "default-src 'none'; sandbox")
                .header(HttpHeaders.CONTENT_DISPOSITION, "inline");
        if (upstream.getHeaders().getContentLength() >= 0) builder.contentLength(upstream.getHeaders().getContentLength());
        return builder.body(stream);
    }

    void requireAccess(long userId, String assetId) {
        if (!assetId.matches("[A-Za-z0-9_-]{16,128}")) {
            throw new BizException(ErrorCode.NOT_FOUND);
        }
        for (String metadata : messageMapper.findAssetMetadata(userId, assetId)) {
            try {
                if (containsReference(objectMapper.readTree(metadata), assetId)) return;
            } catch (Exception ignored) {
                // 作品说明：格式损坏的旧消息元数据不能赋予资产访问权限。
            }
        }
        throw new BizException(ErrorCode.NOT_FOUND);
    }

    static boolean containsReference(JsonNode metadata, String assetId) {
        if (metadata == null) return false;
        for (JsonNode ref : metadata.path("references")) {
            if (hasAsset(ref, assetId)) return true;
        }
        for (JsonNode evidence : List.of(metadata.path("evidence"), metadata.path("validation").path("evidence"))) {
            for (JsonNode item : evidence) {
                if ("sql".equals(item.path("type").asText()) && hasFactSource(item.path("facts"), assetId)) return true;
                if (!List.of("reference", "chart", "asset").contains(item.path("type").asText())) continue;
                if (hasAsset(item, assetId) || hasAsset(item.path("source"), assetId)
                        || hasAsset(item.path("chart_data"), assetId)) return true;
            }
        }
        if (hasFactSource(metadata.path("facts"), assetId)
                || hasFactSource(metadata.path("validation").path("facts"), assetId)) return true;
        if (hasAsset(metadata.path("chart_data"), assetId)) return true;
        for (JsonNode chart : metadata.path("chart_data_list")) {
            if (hasAsset(chart, assetId)) return true;
        }
        for (JsonNode image : metadata.path("images")) {
            if (image.isTextual() && image.asText().equals("/api/assets/" + assetId)) return true;
        }
        return false;
    }

    private static boolean hasAsset(JsonNode node, String assetId) {
        return node != null && assetId.equals(node.path("asset_id").asText());
    }

    private static boolean hasFactSource(JsonNode facts, String assetId) {
        for (JsonNode fact : facts) {
            if (hasAsset(fact.path("source"), assetId)) return true;
        }
        return false;
    }
}
