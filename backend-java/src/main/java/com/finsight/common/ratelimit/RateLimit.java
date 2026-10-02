package com.finsight.common.ratelimit;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** 作品说明：滑动窗口限流（按当前登录用户维度）。 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RateLimit {

    /** 作品说明：业务名，组成 Redis key 的一部分 */
    String name();

    /** 作品说明：窗口时长（秒） */
    int windowSeconds() default 60;

    /** 作品说明：窗口内最大请求数 */
    int limit() default 10;

    RateLimitKey keyBy() default RateLimitKey.USER;

    String message() default "请求过于频繁，请稍后再试";
}
