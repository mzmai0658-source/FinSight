package com.finsight.meta;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.finsight.chat.AgentClient;
import com.finsight.common.api.ApiResponse;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Map;

/** 作品说明：门户元信息：健康状态（聚合 Python 微服务）与精选示例问题。免登录可访问。 */
@Slf4j
@Tag(name = "元信息")
@RestController
@RequestMapping("/api/meta")
@RequiredArgsConstructor
public class MetaController {

    private final AgentClient agentClient;
    private final ObjectMapper objectMapper;
    private final JdbcTemplate jdbcTemplate;

    @Operation(summary = "按当前数据生成示例问题")
    @GetMapping("/examples")
    public ApiResponse<Map<String, Object>> examples() {
        try {
            List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                    "SELECT stock_abbr, MAX(report_year) AS last_year "
                    + "FROM income_sheet WHERE report_period='FY' GROUP BY stock_code, stock_abbr ORDER BY stock_code LIMIT 3");
            if (!rows.isEmpty()) {
                List<Map<String, String>> examples = new java.util.ArrayList<>();
                int index = 1;
                for (Map<String, Object> row : rows) {
                    String company = String.valueOf(row.get("stock_abbr"));
                    String year = String.valueOf(row.get("last_year"));
                    examples.add(Map.of(
                            "id", "ex-" + index,
                            "type", "查数字",
                            "question", company + year + "年营收多少"));
                    index++;
                }
                return ApiResponse.ok(Map.of("examples", examples));
            }
        } catch (Exception e) {
            log.warn("[meta] 示例数据暂不可读取: {}", e.getClass().getSimpleName());
        }
        return ApiResponse.ok(Map.of("examples", List.of(
                Map.of("id", "ex-help", "type", "使用说明", "question", "如何指定公司、年份、报告期和指标来查询财报？"))));
    }

    @Operation(summary = "聚合健康状态（Java + Python 微服务）")
    @GetMapping("/health")
    public ApiResponse<JsonNode> health() {
        try {
            JsonNode python = objectMapper.readTree(agentClient.fetchHealthRaw());
            return ApiResponse.ok(python);
        } catch (Exception e) {
            log.warn("[meta] Python 健康检查不可达: {}", e.toString());
            ObjectNode degraded = objectMapper.createObjectNode();
            for (String key : new String[]{"service", "database", "knowledge_base", "llm", "examples"}) {
                ObjectNode item = degraded.putObject(key);
                item.put("ok", false);
                item.put("detail", "AI 微服务暂不可达");
            }
            return ApiResponse.ok(degraded);
        }
    }
}
