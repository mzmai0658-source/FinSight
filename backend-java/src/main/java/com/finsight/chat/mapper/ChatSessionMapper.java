package com.finsight.chat.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.finsight.chat.entity.ChatSession;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface ChatSessionMapper extends BaseMapper<ChatSession> {
    @Select("SELECT id FROM chat_session WHERE id=#{id} AND deleted=0 FOR UPDATE")
    Long lockExisting(long id);

    @Select("SELECT COUNT(*) FROM chat_turn WHERE session_id=#{id} AND active_slot=1")
    int activeTurns(long id);
}
