package com.finsight.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.time.Duration;

@Data
@Component
@ConfigurationProperties(prefix = "finsight.agent")
public class AgentProperties {

    /** 作品说明：Python AI 微服务内网地址 */
    private String baseUrl = "http://127.0.0.1:8000";

    /** 作品说明：与 Python 内部 API 共用的服务令牌，由启动环境提供。 */
    private String internalApiToken = "";

    private Duration connectTimeout = Duration.ofSeconds(5);

    /** 作品说明：SSE 整轮对话的读超时 */
    private Duration responseTimeout = Duration.ofSeconds(300);
}
