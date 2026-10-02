package com.finsight.auth;

/** 作品说明：认证通过后挂在 SecurityContext 上的当前用户快照。 */
public record LoginUser(long userId, String username, String role, String tokenId) {

    public boolean isAdmin() {
        return "ADMIN".equals(role);
    }
}
