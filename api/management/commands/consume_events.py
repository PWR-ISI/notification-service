"""
Long-poll the configured SQS queue and turn domain events into notifications.

Each message is an SNS-to-SQS envelope (JSON) whose actual payload is nested
under "Message". We parse that, look up the handler in api.handlers.HANDLERS,
and only delete the message on success — failures leave it for SQS redelivery.

Run as a separate worker process:
    python manage.py consume_events
"""
import json
import logging
import signal
import time

from django.core.management.base import BaseCommand

from common.aws import aws_client
from api.handlers import HANDLERS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume domain events from SQS and create notifications."

    def add_arguments(self, parser):
        parser.add_argument("--max-messages", type=int, default=10)
        parser.add_argument("--wait-time", type=int, default=20)
        parser.add_argument("--visibility-timeout", type=int, default=60)
        parser.add_argument("--once", action="store_true", help="Drain once and exit.")

    def handle(self, *args, **opts):
        import os
        queue_url = os.getenv("EVENTS_SQS_QUEUE_URL", "")
        if not queue_url:
            self.stderr.write("EVENTS_SQS_QUEUE_URL is not configured; exiting.")
            return

        sqs = aws_client("sqs")
        running = {"flag": True}

        def _stop(signum, frame):
            running["flag"] = False

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

        self.stdout.write(f"Consuming from {queue_url} (handlers: {sorted(HANDLERS)})")

        while running["flag"]:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=opts["max_messages"],
                WaitTimeSeconds=opts["wait_time"],
                VisibilityTimeout=opts["visibility_timeout"],
                MessageAttributeNames=["All"],
            )
            messages = resp.get("Messages", [])
            for msg in messages:
                if not self._process(msg, HANDLERS):
                    continue  # leave for redelivery
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])

            if opts["once"]:
                break
            if not messages:
                time.sleep(0.1)

    def _process(self, msg, handlers) -> bool:
        try:
            body = json.loads(msg["Body"])
        except (json.JSONDecodeError, KeyError):
            logger.exception("Invalid SQS message body; dropping.")
            return True

        # SNS-to-SQS wraps the payload under "Message"; raw SQS publishes don't.
        if isinstance(body, dict) and "Message" in body:
            try:
                envelope = json.loads(body["Message"])
            except json.JSONDecodeError:
                logger.exception("Invalid SNS Message envelope; dropping.")
                return True
        else:
            envelope = body

        event_type = envelope.get("event_type")
        handler = handlers.get(event_type)
        if not handler:
            logger.info("No handler for event_type=%s; ignoring.", event_type)
            return True

        try:
            handler(envelope.get("payload") or {}, envelope=envelope)
            return True
        except Exception:
            logger.exception("Handler for %s raised; leaving for redelivery.", event_type)
            return False
