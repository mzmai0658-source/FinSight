package com.finsight.etl;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.MybatisSqlSessionFactoryBuilder;
import com.finsight.etl.entity.EtlTask;
import com.finsight.etl.mapper.EtlTaskMapper;
import org.apache.ibatis.mapping.Environment;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.transaction.jdbc.JdbcTransactionFactory;
import org.junit.jupiter.api.*;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.*;

/** Uses real SQL transactions, without contacting the installed MySQL service. */
class FinancialSubmissionMapperTest {
    SqlSessionFactory factory;
    SqlSession session;
    EtlTaskMapper mapper;

    @BeforeEach void database() throws Exception {
        var source = new DriverManagerDataSource("jdbc:h2:mem:submission_" + UUID.randomUUID()
                + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", "");
        var configuration = new MybatisConfiguration();
        configuration.setMapUnderscoreToCamelCase(true);
        configuration.setEnvironment(new Environment("test", new JdbcTransactionFactory(), source));
        configuration.addMapper(EtlTaskMapper.class);
        factory = new MybatisSqlSessionFactoryBuilder().build(configuration);
        session = factory.openSession(); mapper = session.getMapper(EtlTaskMapper.class);
        try (var statement = session.getConnection().createStatement()) {
            statement.execute("""
                CREATE TABLE etl_task (
                  id BIGINT PRIMARY KEY, task_uid VARCHAR(36), file_name VARCHAR(255), file_path VARCHAR(500),
                  file_type VARCHAR(16), status VARCHAR(16), run_token VARCHAR(36), retryable BOOLEAN DEFAULT FALSE,
                  attempt_count INT DEFAULT 0, stock_code VARCHAR(20), report_year INT, review_status VARCHAR(16),
                  candidate_id CHAR(64), source_sha256 CHAR(64), company VARCHAR(255), report_period VARCHAR(8),
                  published_version VARCHAR(255), publication_receipt_json VARCHAR(4000), message VARCHAR(2000),
                  steps_json VARCHAR(4000), created_by BIGINT, started_at TIMESTAMP, finished_at TIMESTAMP,
                  created_at TIMESTAMP, updated_at TIMESTAMP)
                """);
            statement.execute("CREATE TABLE financial_submission_operation_lock (id TINYINT PRIMARY KEY)");
            statement.execute("INSERT INTO financial_submission_operation_lock(id) VALUES (1)");
        }
        // Direct JDBC fixture writes are invisible to MyBatis' dirty tracking.
        session.commit(true);
    }

    @AfterEach void close() { if (session != null) session.close(); }

    @Test void rejectedFailureCannotBeClaimedByStaleMqDelivery() {
        insert(1,"financial","FAILED","REJECTED",true);
        assertThat(mapper.claim(1,"new-attempt",LocalDateTime.now())).isZero();
    }

    @Test void publishedPartialCannotBeRequeuedByLegacyAdminRoute() {
        insert(1,"financial","PARTIAL","PUBLISHED",false);
        assertThat(mapper.prepareRetry(1)).isZero();
    }

    @Test void successfulRetryAtomicallyResetsReviewStateAndCannotBeClaimedTwice() {
        insert(1,"financial","PARTIAL","WAITING",false);
        assertThat(mapper.prepareRetry(1)).isEqualTo(1);
        assertThat(mapper.selectById(1).getReviewStatus()).isEqualTo("PROCESSING");
        assertThat(mapper.claim(1,"attempt",LocalDateTime.now())).isEqualTo(1);
        assertThat(mapper.claim(1,"other-attempt",LocalDateTime.now())).isZero();
        assertThat(mapper.selectById(1).getRunToken()).isEqualTo("attempt");
    }

    @Test void researchRetainsExistingClaimAndRetryTransitions() {
        insert(1,"research","FAILED",null,true);
        assertThat(mapper.claim(1,"research-attempt",LocalDateTime.now())).isEqualTo(1);
        assertThat(mapper.selectById(1).getReviewStatus()).isNull();
        assertThat(mapper.prepareRetry(1)).isZero();
    }

    @Test void financialRowLockMapsOwnershipAndCandidateMetadataAndExcludesResearch() {
        insert(1,"financial","SUCCESS","WAITING",false);
        insert(2,"research","SUCCESS",null,false);
        assertThat(mapper.lockFinancialSubmission(1).getCreatedBy()).isEqualTo(7L);
        assertThat(mapper.lockFinancialSubmission(1).getCandidateId()).isEqualTo("a".repeat(64));
        assertThat(mapper.lockFinancialSubmission(2)).isNull();
    }

    @Test void operationLockPreventsConcurrentAdminTransactionsFromPassing() throws Exception {
        assertThat(mapper.lockSubmissionOperations()).isEqualTo(1);
        try (var contender = factory.openSession()) {
            try (var statement = contender.getConnection().createStatement()) { statement.execute("SET LOCK_TIMEOUT 50"); }
            var competingMapper = contender.getMapper(EtlTaskMapper.class);
            assertThatThrownBy(competingMapper::lockSubmissionOperations).hasCauseInstanceOf(java.sql.SQLTimeoutException.class);
            session.commit(true);
            assertThat(competingMapper.lockSubmissionOperations()).isEqualTo(1);
        }
    }

    void insert(long id, String type, String status, String review, boolean retryable) {
        var task = new EtlTask(); task.setId(id);task.setFileType(type);task.setStatus(status);task.setReviewStatus(review);
        task.setCreatedBy(7L);task.setRetryable(retryable);task.setAttemptCount(0);task.setCandidateId("a".repeat(64));
        task.setSourceSha256("a".repeat(64));mapper.insert(task);session.commit();
    }
}
