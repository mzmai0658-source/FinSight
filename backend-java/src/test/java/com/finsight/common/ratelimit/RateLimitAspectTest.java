package com.finsight.common.ratelimit;

import com.finsight.auth.LoginUser;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.core.env.Environment;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.security.authentication.TestingAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.lang.reflect.Method;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class RateLimitAspectTest {

    @Mock
    private StringRedisTemplate redisTemplate;
    @Mock
    private Environment environment;

    @AfterEach
    void tearDown() {
        RequestContextHolder.resetRequestAttributes();
        SecurityContextHolder.clearContext();
    }

    @Test
    void rateLimitCanUseClientIpKeyForAnonymousEndpoints() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setRemoteAddr("10.0.0.8");
        request.addHeader("X-Forwarded-For", "203.0.113.9, 10.0.0.8");
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
        when(redisTemplate.execute(any(DefaultRedisScript.class), any(), eq("60000"), eq("5"), any()))
                .thenReturn(1L);
        RateLimitAspect aspect = new RateLimitAspect(redisTemplate, environment);

        aspect.checkRateLimit(rateLimitAnnotation("login"));

        ArgumentCaptor<List<String>> keys = ArgumentCaptor.forClass(List.class);
        verify(redisTemplate).execute(any(DefaultRedisScript.class), keys.capture(), eq("60000"), eq("5"), any());
        assertThat(keys.getValue()).containsExactly("rl:auth-login:ip:203.0.113.9");
    }

    @Test
    void dailyQuotaUsesLuaForAtomicIncrementAndExpire() throws Exception {
        SecurityContextHolder.getContext().setAuthentication(
                new TestingAuthenticationToken(new LoginUser(7L, "alice", "USER", "t1"), null));
        when(environment.getProperty(eq("finsight.quota.chat-per-day"), eq(Integer.class), eq(100)))
                .thenReturn(100);
        when(redisTemplate.execute(any(DefaultRedisScript.class), any(), eq("100"), eq("93600")))
                .thenReturn(1L);
        RateLimitAspect aspect = new RateLimitAspect(redisTemplate, environment);

        aspect.checkDailyQuota(dailyQuotaAnnotation("chat"));

        String day = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"));
        ArgumentCaptor<List<String>> keys = ArgumentCaptor.forClass(List.class);
        verify(redisTemplate).execute(any(DefaultRedisScript.class), keys.capture(), eq("100"), eq("93600"));
        assertThat(keys.getValue()).containsExactly("quota:chat:7:" + day);
    }

    private static RateLimit rateLimitAnnotation(String methodName) throws NoSuchMethodException {
        return TestEndpoints.class.getDeclaredMethod(methodName).getAnnotation(RateLimit.class);
    }

    private static DailyQuota dailyQuotaAnnotation(String methodName) throws NoSuchMethodException {
        return TestEndpoints.class.getDeclaredMethod(methodName).getAnnotation(DailyQuota.class);
    }

    static class TestEndpoints {
        @RateLimit(name = "auth-login", keyBy = RateLimitKey.IP, windowSeconds = 60, limit = 5)
        void login() {
        }

        @DailyQuota(name = "chat", limit = 100, exemptAdmin = false)
        void chat() {
        }
    }
}
