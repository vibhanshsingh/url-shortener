"""
Why explicit topic creation instead of relying on Kafka's
auto.create.topics.enable: auto-created topics get a default partition
count (often just 1), which would silently defeat the whole point of
partitioning click-events by short_code. Explicitly creating topics
with a chosen partition count is what real production Kafka usage
looks like — topic configuration is treated as infrastructure, not an
accident of whichever service happens to publish to it first.

This is called from both the API's startup (producer side) and the
consumer worker's startup, and is safe to call multiple times —
"topic already exists" is caught and ignored, not treated as an error.
"""

import logging

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from app.core.config import settings

logger = logging.getLogger(__name__)


async def ensure_topics_exist() -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers)
    await admin.start()
    try:
        topics = [
            # 3 partitions: enough to demonstrate parallel consumption
            # across multiple consumer instances in Milestone 14's load
            # test, without over-provisioning for a local learning
            # project. replication_factor=1 because we're running a
            # single-broker Kafka locally — production would use 3.
            NewTopic(
                name=settings.kafka_click_events_topic,
                num_partitions=3,
                replication_factor=1,
            ),
            NewTopic(
                name=settings.kafka_click_events_dlq_topic,
                num_partitions=1,
                replication_factor=1,
            ),
        ]
        try:
            await admin.create_topics(topics)
            logger.info("Kafka topics created: %s", [t.name for t in topics])
        except TopicAlreadyExistsError:
            logger.info("Kafka topics already exist, skipping creation")
    finally:
        await admin.close()
