package com.finsight.config;

import com.finsight.common.mq.MqOutboxConfirmHandler;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Slf4j
@Configuration
public class RabbitConfig {

    public static final String EXCHANGE = "finsight.topic";
    public static final String DLX = "finsight.dlx";
    public static final String DEAD_QUEUE = "finsight.dead.queue";

    public static final String CHAT_LOG_QUEUE = "chat.log.queue";
    public static final String CHAT_LOG_RK = "chat.log";
    public static final String CHAT_TITLE_QUEUE = "chat.title.queue";
    public static final String CHAT_TITLE_RK = "chat.title";
    public static final String ETL_TASK_QUEUE = "etl.task.queue";
    public static final String ETL_TASK_RK = "etl.task";
    public static final String ADVISOR_REPORT_QUEUE = "advisor.report.queue";
    public static final String ADVISOR_REPORT_RK = "advisor.report";

    @Bean
    public TopicExchange finsightExchange() {
        return new TopicExchange(EXCHANGE, true, false);
    }

    @Bean
    public TopicExchange deadLetterExchange() {
        return new TopicExchange(DLX, true, false);
    }

    @Bean
    public Queue deadQueue() {
        return QueueBuilder.durable(DEAD_QUEUE).build();
    }

    @Bean
    public Binding deadBinding() {
        return BindingBuilder.bind(deadQueue()).to(deadLetterExchange()).with("#");
    }

    @Bean
    public Queue chatLogQueue() {
        return durableQueue(CHAT_LOG_QUEUE, CHAT_LOG_RK);
    }

    @Bean
    public Binding chatLogBinding() {
        return BindingBuilder.bind(chatLogQueue()).to(finsightExchange()).with(CHAT_LOG_RK);
    }

    @Bean
    public Queue chatTitleQueue() {
        return durableQueue(CHAT_TITLE_QUEUE, CHAT_TITLE_RK);
    }

    @Bean
    public Binding chatTitleBinding() {
        return BindingBuilder.bind(chatTitleQueue()).to(finsightExchange()).with(CHAT_TITLE_RK);
    }

    @Bean
    public Queue etlTaskQueue() {
        return durableQueue(ETL_TASK_QUEUE, ETL_TASK_RK);
    }

    @Bean
    public Binding etlTaskBinding() {
        return BindingBuilder.bind(etlTaskQueue()).to(finsightExchange()).with(ETL_TASK_RK);
    }

    @Bean
    public Queue advisorReportQueue() {
        return durableQueue(ADVISOR_REPORT_QUEUE, ADVISOR_REPORT_RK);
    }

    @Bean
    public Binding advisorReportBinding() {
        return BindingBuilder.bind(advisorReportQueue()).to(finsightExchange()).with(ADVISOR_REPORT_RK);
    }

    @Bean
    public Jackson2JsonMessageConverter jacksonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public RabbitTemplate rabbitTemplate(ConnectionFactory connectionFactory,
                                         Jackson2JsonMessageConverter converter,
                                         MqOutboxConfirmHandler outboxConfirmHandler) {
        RabbitTemplate template = new RabbitTemplate(connectionFactory);
        template.setMessageConverter(converter);
        template.setConfirmCallback((correlation, ack, cause) -> {
            outboxConfirmHandler.handleConfirm(correlation, ack, cause);
            if (!ack) {
                log.warn("[mq] publisher confirm nack: {}", cause);
            }
        });
        template.setReturnsCallback(returned -> {
            outboxConfirmHandler.handleReturned(returned);
            log.warn("[mq] returned message rk={} message={}",
                    returned.getRoutingKey(), returned.getMessage());
        });
        return template;
    }

    private Queue durableQueue(String queueName, String deadLetterRoutingKey) {
        return QueueBuilder.durable(queueName)
                .deadLetterExchange(DLX)
                .deadLetterRoutingKey(deadLetterRoutingKey)
                .build();
    }
}
