"""
Runs as a standalone process (see the `consumer` service in
docker-compose.yml), completely separate from the FastAPI app. This is
the concrete implementation of the "workers/" folder from the original
clean-architecture spec: something that scales and fails independently
of HTTP traffic.

Delivery semantics: AT-LEAST-ONCE. We only commit a message's offset
AFTER successfully writing it to Postgres. If the process crashes
between the write and the commit, Kafka will redeliver that message on
restart — meaning duplicate click_events rows are possible in that
narrow window. For click analytics, an occasionally-inflated count
during a crash is an acceptable trade-off; it would NOT be acceptable
for something like billing, which is exactly the kind of distinction
worth being able to articulate in an interview.

Poison-pill handling: a message is retried up to MAX_RETRIES times. If
it still fails, it's published to the DLQ topic and the offset is
committed anyway — this sacrifices that one message's data rather than
blocking every message behind it on the same partition forever.
"""

import asyncio
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.events.admin import ensure_topics_exist
from app.events.schemas import ClickEvent
from app.events.user_agent_parser import parse_browser, parse_device_type
from app.models.click_event import ClickEvent as ClickEventModel
from app.repository.url_repository import URLRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0


async def process_message(raw_value: bytes, dlq_producer: AIOKafkaProducer) -> None:
    """One message, start to finish: parse -> resolve URL -> insert.
    Retries transient failures (e.g. a momentary DB blip); after
    MAX_RETRIES, routes to the DLQ instead of blocking the partition
    forever on a message that will never succeed."""
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            event = ClickEvent.from_kafka_value(raw_value)

            async with AsyncSessionLocal() as session:
                repository = URLRepository(session)
                url_row = await repository.get_by_short_code_any_status(event.short_code)

                if url_row is None:
                    # The click referenced a short_code that doesn't
                    # exist in our database at all — shouldn't happen
                    # under normal operation (the redirect endpoint
                    # only publishes events for codes it just
                    # successfully resolved), but treated as a
                    # permanent, non-retryable failure rather than a
                    # transient one: retrying won't make a nonexistent
                    # URL appear.
                    logger.warning(
                        "No URL found for short_code=%s; routing to DLQ", event.short_code
                    )
                    await _send_to_dlq(dlq_producer, raw_value, reason="unknown_short_code")
                    return

                click_row = ClickEventModel(
                    url_id=url_row.id,
                    clicked_at=event.timestamp,
                    ip_address=event.ip_address,
                    user_agent=event.user_agent,
                    referrer=event.referrer,
                    country=None,  # would need a GeoIP lookup — out of scope here
                    device_type=parse_device_type(event.user_agent),
                    browser=parse_browser(event.user_agent),
                )
                session.add(click_row)
                await session.commit()

            logger.info(
                "Processed click event: short_code=%s attempt=%d", event.short_code, attempt
            )
            return  # success — exit the retry loop

        except Exception as exc:  # noqa: BLE001 — deliberately broad, see module docstring
            last_error = exc
            logger.warning(
                "Attempt %d/%d failed processing click event: %s", attempt, MAX_RETRIES, exc
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)  # simple linear backoff

    # Exhausted all retries — this is a genuinely broken message
    # (malformed JSON, persistent DB issue, etc). Route to DLQ instead
    # of losing it silently or blocking the partition indefinitely.
    logger.error("Exhausted retries, routing to DLQ. Last error: %s", last_error)
    await _send_to_dlq(dlq_producer, raw_value, reason=str(last_error))


async def _send_to_dlq(producer: AIOKafkaProducer, raw_value: bytes, reason: str) -> None:
    try:
        await producer.send_and_wait(
            topic=settings.kafka_click_events_dlq_topic,
            value=raw_value,
            headers=[("failure_reason", reason.encode("utf-8")[:1000])],
        )
    except Exception:
        # If even the DLQ publish fails, there's nothing left to do
        # but log loudly — this is the true worst case (message lost).
        logger.exception("Failed to publish to DLQ as well — message is lost: %r", raw_value)


async def run_consumer() -> None:
    # Idempotent — safe even if the API container already created
    # these topics. Guards against the consumer starting first.
    await ensure_topics_exist()

    consumer = AIOKafkaConsumer(
        settings.kafka_click_events_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group_id,
        # Manual commit is what makes at-least-once semantics real —
        # enable_auto_commit=True would commit offsets on a timer
        # regardless of whether processing actually succeeded, which
        # would silently become at-most-once (message loss on crash)
        # instead.
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    dlq_producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)

    await consumer.start()
    await dlq_producer.start()
    logger.info(
        "Consumer started. topic=%s group_id=%s",
        settings.kafka_click_events_topic,
        settings.kafka_consumer_group_id,
    )

    try:
        async for message in consumer:
            await process_message(message.value, dlq_producer)
            # Commit AFTER processing (success or routed-to-DLQ) — this
            # is the line that actually implements at-least-once:
            # nothing is marked "done" until we've handled it one way
            # or another.
            await consumer.commit()
    finally:
        await consumer.stop()
        await dlq_producer.stop()
        logger.info("Consumer stopped")


if __name__ == "__main__":
    asyncio.run(run_consumer())
