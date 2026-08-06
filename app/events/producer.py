"""
This producer is called from a FastAPI BackgroundTask, AFTER the
redirect response has already been sent to the browser (see
app/api/routes/redirect.py). That ordering is what guarantees a Kafka
outage can never slow down or break a redirect — by the time this
code runs, the user already has their 301.

The try/except around publish() is deliberate and important: if we let
an exception here propagate, FastAPI would log it as an unhandled
background task error, which is merely noisy — the response was
already sent, so there's no user-facing failure either way. We catch
and log explicitly so an on-call engineer sees a clear "click event
lost" log line instead of a confusing background-task traceback.
"""

import logging

from aiokafka import AIOKafkaProducer

from app.core.config import settings
from app.events.schemas import ClickEvent

logger = logging.getLogger(__name__)


class KafkaEventProducer:
    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            # acks=1: leader broker confirms the write before we
            # consider publish successful. acks="all" would be more
            # durable (waits for replicas) but our single-broker local
            # setup has no replicas to wait for — acks=1 is the
            # meaningful choice here, with a note that production
            # (replication_factor=3) would use acks="all".
            acks=-1,
            enable_idempotence=True,  # avoids duplicate messages on producer-side retries
        )
        await self._producer.start()
        logger.info("Kafka producer started")

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            logger.info("Kafka producer stopped")

    def is_connected(self) -> bool:
        """
        Best-effort check for the readiness endpoint. Real connection
        health inside aiokafka isn't exposed as a single clean boolean,
        so this checks the one thing we can cheaply verify: has
        start() been called and not yet stop()'d. Good enough to catch
        "producer never started" or "producer already shut down"; it
        won't catch every possible half-broken connection state — a
        limitation worth naming rather than pretending this is a
        complete health check.
        """
        return self._producer is not None

    async def publish_click_event(self, event: ClickEvent) -> None:
        if self._producer is None:
            logger.error("Kafka producer not started; dropping click event for %s", event.short_code)
            return

        try:
            await self._producer.send_and_wait(
                topic=settings.kafka_click_events_topic,
                key=event.short_code.encode("utf-8"),  # partitioning key — see admin.py's docstring
                value=event.to_kafka_value(),
            )
        except Exception:
            # Deliberately broad: ANY failure to publish a click event
            # must never propagate up and affect the caller. This is
            # the concrete implementation of "the redirect never waits
            # on analytics" — extended to "and analytics failures never
            # affect the redirect" either.
            logger.exception("Failed to publish click event for short_code=%s", event.short_code)


# Module-level singleton, mirroring the pattern used for the Redis
# client and the SQLAlchemy engine — one producer, reused across every
# request, its connection lifecycle tied to the app's startup/shutdown.
kafka_producer = KafkaEventProducer()


def get_kafka_producer() -> KafkaEventProducer:
    return kafka_producer
