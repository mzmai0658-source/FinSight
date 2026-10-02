package com.finsight.etl.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.finsight.etl.entity.EtlTask;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;

@Mapper
public interface EtlTaskMapper extends BaseMapper<EtlTask> {
    @Update("""
            UPDATE etl_task SET status='RUNNING', run_token=#{token}, started_at=#{started},
              finished_at=NULL, retryable=FALSE, attempt_count=attempt_count+1
            WHERE id=#{id} AND (status='PENDING' OR (status='FAILED' AND retryable=TRUE))
            """)
    int claim(@Param("id") long id, @Param("token") String token, @Param("started") LocalDateTime started);

    @Update("""
            UPDATE etl_task SET status='PENDING', message='', started_at=NULL, finished_at=NULL,
              run_token=NULL, retryable=FALSE
            WHERE id=#{id} AND status IN ('FAILED','PARTIAL')
            """)
    int prepareRetry(@Param("id") long id);
}
