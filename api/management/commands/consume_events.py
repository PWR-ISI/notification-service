"""
Long-poll the configured SQS queue and dispatch to api.handlers.HANDLERS.

Ported from appointment-service/common/management/commands/consume_events.py
so all services follow the same shape:

  - SNS-to-SQS deliveries wrap the payload under "Message" (a JSON string);
    raw SQS publishes (e.g. payment-service writing straight to its success
    queue) do NOT have that wrapping. We handle both.
  - Each message expects an envelope: {event_type, event_id, occurred_at,
    payload}. payment-service publishes the payload directly without an
    envelope; we synthesise one (event_type = payload['event']) so handlers
    see a uniform shape.
  - On handler exception the message is left in the queue for SQS redelivery;
    only successful processing deletes it.

Runs as a sidecar container in docker-compose / a separate ECS task in prod.
"""
from __future__ import annotations

import json
import logging
import signal
import time

import boto3
from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def _aws_client(service: str):
    """Build a boto3 client honouring AWS_ENDPOINT_URL for LocalStack."""
    import os
    return boto3.client(
        service,
        endpoint_url=os.getenv('AWS_ENDPOINT_URL') or None,
        region_name=os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1',
    )


class Command(BaseCommand):
    help = "Consume domain events from SQS and dispatch to api.handlers.HANDLERS."

    def add_arguments(self, parser):
        parser.add_argument("--max-messages", type=int, default=10)
        parser.add_argument("--wait-time", type=int, default=20)
        parser.add_argument("--visibility-timeout", type=int, default=60)
        parser.add_argument("--once", action="store_true", help="Drain once and exit (testing).")

    def handle(self, *args, **opts):
        # Lazy import so tests can stub HANDLERS without triggering DB access
        # at startup (some handler modules touch the ORM at import time).
        from api.handlers import HANDLERS

        queue_url = settings.EVENTS_SQS_QUEUE_URL
        if not queue_url:
            self.stderr.write("EVENTS_SQS_QUEUE_URL is not configured; exiting.")
            return

        sqs = _aws_client('sqs')
        running = {"flag": True}

        def _stop(_signum, _frame):
            self.stdout.write("Stop signal received; finishing in-flight batch.")
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
                if self._process(msg, HANDLERS):
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
                # else leave for redelivery — visibility timeout will expire

            if opts["once"]:
                break
            if not messages:
                time.sleep(0.1)

    def _process(self, msg, handlers) -> bool:
        """Returns True if the message should be deleted (handled / unprocessable)."""
        try:
            body = json.loads(msg["Body"])
        except (json.JSONDecodeError, KeyError):
            logger.exception("Invalid SQS body; dropping. raw=%r", msg.get("Body"))
            return True  # don't loop on garbage

        # Unwrap SNS-to-SQS envelope if present.
        if isinstance(body, dict) and "Message" in body and "TopicArn" in body:
            try:
                body = json.loads(body["Message"])
            except (json.JSONDecodeError, TypeError):
                logger.exception("SNS envelope's Message field is not JSON; dropping.")
                return True

        # Two flavours of envelope:
        #   (a) {event_type, event_id, occurred_at, payload}  (appointment-service style)
        #   (b) {event: "payment.success", ...domain fields...}  (payment-service raw)
        if "event_type" in body and "payload" in body:
            event_type = body["event_type"]
            payload    = body["payload"]
        elif "event" in body:
            event_type = body["event"]
            payload    = body
        else:
            logger.error("Message has neither event_type nor event; dropping. body=%r", body)
            return True

        handler = handlers.get(event_type)
        if handler is None:
            # Unknown event types are normal in a shared topic — just ack.
            logger.debug("No handler for event_type=%s; acknowledging.", event_type)
            return True

        try:
            return handler(payload)
        except Exception:
            logger.exception("Handler %s raised; leaving message for redelivery.", event_type)
            return False
