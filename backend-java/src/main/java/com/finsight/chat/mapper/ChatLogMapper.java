package com.finsight.chat.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.finsight.chat.entity.ChatLog;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface ChatLogMapper extends BaseMapper<ChatLog> {
}
