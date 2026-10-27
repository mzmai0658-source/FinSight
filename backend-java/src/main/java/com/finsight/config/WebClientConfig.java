package com.finsight.config;

import io.netty.channel.ChannelOption;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

@Configuration
@RequiredArgsConstructor
public class WebClientConfig {

    private final AgentProperties agentProperties;

    @Value("${finsight.etl.request-timeout:2h}")
    private java.time.Duration etlRequestTimeout = java.time.Duration.ofHours(2);

    @Bean
    public WebClient agentWebClient() {
        requireInternalToken();
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS,
                        (int) agentProperties.getConnectTimeout().toMillis())
                .responseTimeout(agentProperties.getResponseTimeout());
        return WebClient.builder()
                .baseUrl(agentProperties.getBaseUrl())
                .defaultHeader("X-Internal-Token", agentProperties.getInternalApiToken())
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                // SSE 长回答可能较大，放宽内存上限
                .codecs(configurer -> configurer.defaultCodecs().maxInMemorySize(8 * 1024 * 1024))
                .build();
    }

    /** OCR 与云端抽取共享可配置的单文件请求截止时间。 */
    @Bean
    public WebClient etlWebClient() {
        requireInternalToken();
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS,
                        (int) agentProperties.getConnectTimeout().toMillis())
                .responseTimeout(etlRequestTimeout);
        return WebClient.builder()
                .baseUrl(agentProperties.getBaseUrl())
                .defaultHeader("X-Internal-Token", agentProperties.getInternalApiToken())
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                // Candidate evidence contains every verified fact and may exceed a chat-sized response.
                .codecs(configurer -> configurer.defaultCodecs().maxInMemorySize(32 * 1024 * 1024))
                .build();
    }

    private void requireInternalToken() {
        if (agentProperties.getInternalApiToken() == null || agentProperties.getInternalApiToken().isBlank()) {
            throw new IllegalStateException("INTERNAL_API_TOKEN must be configured for the Python service connection");
        }
    }
}
