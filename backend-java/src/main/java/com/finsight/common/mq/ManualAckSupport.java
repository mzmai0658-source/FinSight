package com.finsight.common.mq;

import com.rabbitmq.client.Channel;
import org.springframework.amqp.AmqpIOException;
import org.springframework.amqp.core.Message;

import java.io.IOException;

public final class ManualAckSupport {

    private ManualAckSupport() {
    }

    public static void ack(Channel channel, Message message) {
        try {
            channel.basicAck(message.getMessageProperties().getDeliveryTag(), false);
        } catch (IOException e) {
            throw new AmqpIOException(e);
        }
    }
}
