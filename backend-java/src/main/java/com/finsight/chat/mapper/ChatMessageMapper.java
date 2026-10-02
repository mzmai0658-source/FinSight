package com.finsight.chat.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.finsight.chat.entity.ChatMessage;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Param;
import java.util.List;

@Mapper
public interface ChatMessageMapper extends BaseMapper<ChatMessage> {
    @Select("""
            SELECT m.metadata FROM chat_message m
            JOIN chat_session s ON s.id = m.session_id
            WHERE s.user_id = #{userId} AND s.deleted = 0 AND m.role = 'assistant'
              AND CAST(m.metadata AS CHAR) LIKE CONCAT('%', #{assetId}, '%')
            """)
    List<String> findAssetMetadata(@Param("userId") long userId, @Param("assetId") String assetId);
}
