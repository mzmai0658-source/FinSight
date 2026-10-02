package com.finsight.common.ratelimit;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 作品说明：每日配额（按当前登录用户维度）。
 * 配额 key 带日期、随自然日轮换并设置 TTL，自动完成"定时重置"。
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface DailyQuota {

    /** 作品说明：业务名，组成 Redis key 的一部分 */
    String name();

    /** 作品说明：每日限额；可被配置项 finsight.quota.{name}-per-day 覆盖 */
    int limit() default 100;

    /** 作品说明：ADMIN 角色是否豁免 */
    boolean exemptAdmin() default true;

    String message() default "今日使用额度已用完，明天再来吧";
}
