package com.finsight.etl;

/** 作品说明：HTTP 响应丢失时，Python 可能仍在执行导入，不能直接认定任务失败。 */
public class EtlExecutionUncertainException extends IllegalStateException {
    public EtlExecutionUncertainException(Throwable cause) {
        super("ETL response unavailable; execution may still be running. Wait for recovery before retrying.", cause);
    }
}
