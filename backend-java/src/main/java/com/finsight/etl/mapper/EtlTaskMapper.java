package com.finsight.etl.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.finsight.etl.entity.EtlTask;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;
import org.apache.ibatis.annotations.Select;

import java.time.LocalDateTime;

@Mapper
public interface EtlTaskMapper extends BaseMapper<EtlTask> {
    @Select("SELECT id FROM financial_submission_operation_lock WHERE id=1 FOR UPDATE")
    Integer lockSubmissionOperations();

    @Select("SELECT * FROM etl_task WHERE id=#{id} AND file_type='financial' FOR UPDATE")
    EtlTask lockFinancialSubmission(@Param("id") long id);

    @Update("""
            UPDATE etl_task SET status='RUNNING', run_token=#{token}, started_at=#{started},
              finished_at=NULL, retryable=FALSE, attempt_count=attempt_count+1,
              review_status=CASE WHEN file_type='financial' THEN 'PROCESSING' ELSE review_status END
            WHERE id=#{id} AND (status='PENDING' OR (status='FAILED' AND retryable=TRUE))
              AND (file_type<>'financial' OR COALESCE(review_status,'PROCESSING') IN ('PROCESSING','WAITING'))
            """)
    int claim(@Param("id") long id, @Param("token") String token, @Param("started") LocalDateTime started);

    @Update("""
            UPDATE etl_task SET status='PENDING', message='', started_at=NULL, finished_at=NULL,
              run_token=NULL, retryable=FALSE,
              review_status=CASE WHEN file_type='financial' THEN 'PROCESSING' ELSE review_status END
            WHERE id=#{id} AND status IN ('FAILED','PARTIAL')
              AND (file_type<>'financial' OR COALESCE(review_status,'PROCESSING') IN ('PROCESSING','WAITING'))
            """)
    int prepareRetry(@Param("id") long id);
}
