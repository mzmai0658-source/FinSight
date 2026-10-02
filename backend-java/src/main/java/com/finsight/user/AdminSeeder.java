package com.finsight.user;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.finsight.user.entity.SysUser;
import com.finsight.user.mapper.SysUserMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/** 作品说明：首次启动播种管理员账号（避免把 BCrypt 哈希硬编码进迁移脚本）。 */
@Slf4j
@Component
@RequiredArgsConstructor
public class AdminSeeder implements ApplicationRunner {

    private final SysUserMapper userMapper;
    private final PasswordEncoder passwordEncoder;

    @Value("${finsight.admin.username}")
    private String adminUsername;

    @Value("${finsight.admin.password}")
    private String adminPassword;

    @Override
    public void run(ApplicationArguments args) {
        Long count = userMapper.selectCount(
                Wrappers.<SysUser>lambdaQuery().eq(SysUser::getUsername, adminUsername));
        if (count != null && count > 0) {
            return;
        }
        SysUser admin = new SysUser();
        admin.setUsername(adminUsername);
        admin.setPasswordHash(passwordEncoder.encode(adminPassword));
        admin.setNickname("管理员");
        admin.setRole(SysUser.ROLE_ADMIN);
        userMapper.insert(admin);
        log.info("已播种管理员账号: {}（请尽快修改默认密码）", adminUsername);
    }
}
