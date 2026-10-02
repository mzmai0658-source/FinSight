package com.finsight.common.api;

import lombok.Getter;
import org.springframework.http.HttpStatus;

/**
 * 作品说明：业务错误码：与 HTTP 状态解耦但显式关联——
 * 网关/前端按 HTTP 状态做粗粒度处理（401 刷新、429 退避），按业务码做细粒度提示。
 */
@Getter
public enum ErrorCode {

    OK(0, "成功", HttpStatus.OK),

    BAD_REQUEST(40000, "请求参数有误", HttpStatus.BAD_REQUEST),
    UNAUTHORIZED(40100, "未登录或登录已过期", HttpStatus.UNAUTHORIZED),
    TOKEN_EXPIRED(40101, "登录已过期，请刷新或重新登录", HttpStatus.UNAUTHORIZED),
    TOKEN_INVALID(40102, "凭证无效", HttpStatus.UNAUTHORIZED),
    FORBIDDEN(40300, "没有访问权限", HttpStatus.FORBIDDEN),
    NOT_FOUND(40400, "资源不存在", HttpStatus.NOT_FOUND),
    CONFLICT(40900, "资源冲突", HttpStatus.CONFLICT),
    RATE_LIMITED(42900, "请求过于频繁，请稍后再试", HttpStatus.TOO_MANY_REQUESTS),
    QUOTA_EXCEEDED(42901, "今日额度已用完", HttpStatus.TOO_MANY_REQUESTS),

    USERNAME_TAKEN(41001, "用户名已被注册", HttpStatus.BAD_REQUEST),
    BAD_CREDENTIALS(41002, "用户名或密码错误", HttpStatus.BAD_REQUEST),
    USER_DISABLED(41003, "账号已被禁用", HttpStatus.FORBIDDEN),
    NOT_ADMIN(41004, "该账号不是管理员", HttpStatus.FORBIDDEN),

    INTERNAL_ERROR(50000, "服务器开小差了，请稍后重试", HttpStatus.INTERNAL_SERVER_ERROR),
    AGENT_UNAVAILABLE(50301, "AI 分析服务暂不可用", HttpStatus.SERVICE_UNAVAILABLE),
    DEPENDENCY_ERROR(50302, "依赖服务异常", HttpStatus.SERVICE_UNAVAILABLE);

    private final int code;
    private final String message;
    private final HttpStatus httpStatus;

    ErrorCode(int code, String message, HttpStatus httpStatus) {
        this.code = code;
        this.message = message;
        this.httpStatus = httpStatus;
    }
}
