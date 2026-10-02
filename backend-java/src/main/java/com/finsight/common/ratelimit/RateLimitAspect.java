package com.finsight.common.ratelimit;

import com.finsight.auth.LoginUser;
import com.finsight.common.api.ErrorCode;
import com.finsight.common.exception.BizException;
import com.finsight.common.security.SecurityUtils;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.springframework.core.env.Environment;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.scripting.support.ResourceScriptSource;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.List;

@Slf4j
@Aspect
@Component
@RequiredArgsConstructor
public class RateLimitAspect {

    private static final DateTimeFormatter DAY_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");
    private static final long DAILY_QUOTA_TTL_SECONDS = 26 * 60 * 60L;

    private final StringRedisTemplate redisTemplate;
    private final Environment environment;

    private final DefaultRedisScript<Long> slidingWindowScript = loadScript(
            "scripts/sliding_window_rate_limit.lua");
    private final DefaultRedisScript<Long> dailyQuotaScript = loadScript(
            "scripts/daily_quota.lua");

    private static DefaultRedisScript<Long> loadScript(String path) {
        DefaultRedisScript<Long> script = new DefaultRedisScript<>();
        script.setScriptSource(new ResourceScriptSource(new ClassPathResource(path)));
        script.setResultType(Long.class);
        return script;
    }

    @Before("@annotation(rateLimit)")
    public void checkRateLimit(RateLimit rateLimit) {
        String key = "rl:%s:%s".formatted(rateLimit.name(), resolveKey(rateLimit.keyBy()));
        try {
            Long allowed = redisTemplate.execute(
                    slidingWindowScript,
                    List.of(key),
                    String.valueOf(rateLimit.windowSeconds() * 1000L),
                    String.valueOf(rateLimit.limit()),
                    String.valueOf(System.currentTimeMillis()));
            if (allowed != null && allowed == 0) {
                throw new BizException(ErrorCode.RATE_LIMITED, rateLimit.message());
            }
        } catch (BizException e) {
            throw e;
        } catch (Exception e) {
            log.warn("[ratelimit] Redis unavailable, fail-open: {}", e.toString());
        }
    }

    @Before("@annotation(dailyQuota)")
    public void checkDailyQuota(DailyQuota dailyQuota) {
        LoginUser user = SecurityUtils.currentUser();
        if (dailyQuota.exemptAdmin() && user.isAdmin()) {
            return;
        }
        int limit = environment.getProperty(
                "finsight.quota." + dailyQuota.name() + "-per-day", Integer.class, dailyQuota.limit());
        String day = LocalDate.now().format(DAY_FORMAT);
        String key = "quota:%s:%d:%s".formatted(dailyQuota.name(), user.userId(), day);
        try {
            Long count = redisTemplate.execute(
                    dailyQuotaScript,
                    List.of(key),
                    String.valueOf(limit),
                    String.valueOf(DAILY_QUOTA_TTL_SECONDS));
            if (count != null && count < 0) {
                throw new BizException(ErrorCode.QUOTA_EXCEEDED, dailyQuota.message());
            }
        } catch (BizException e) {
            throw e;
        } catch (Exception e) {
            log.warn("[quota] Redis unavailable, fail-open: {}", e.toString());
        }
    }

    private String resolveKey(RateLimitKey keyBy) {
        String ip = safeKeyPart(clientIp());
        LoginUser user = currentUserOrNull();
        return switch (keyBy) {
            case IP -> "ip:" + ip;
            case USER_IP -> user == null ? "ip:" + ip : "user:" + user.userId() + ":ip:" + ip;
            case USER -> user == null ? "ip:" + ip : "user:" + user.userId();
        };
    }

    private LoginUser currentUserOrNull() {
        try {
            return SecurityUtils.currentUser();
        } catch (Exception ignored) {
            return null;
        }
    }

    private String clientIp() {
        if (RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attrs) {
            HttpServletRequest request = attrs.getRequest();
            String forwarded = firstHeaderValue(request.getHeader("X-Forwarded-For"));
            if (!forwarded.isBlank()) {
                return forwarded;
            }
            String realIp = firstHeaderValue(request.getHeader("X-Real-IP"));
            if (!realIp.isBlank()) {
                return realIp;
            }
            String remote = request.getRemoteAddr();
            return remote == null || remote.isBlank() ? "unknown" : remote;
        }
        return "unknown";
    }

    private static String firstHeaderValue(String value) {
        if (value == null || value.isBlank()) {
            return "";
        }
        return value.split(",")[0].strip();
    }

    private static String safeKeyPart(String value) {
        return String.valueOf(value == null ? "unknown" : value).replaceAll("[^A-Za-z0-9_.:-]", "_");
    }
}
