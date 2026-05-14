#!/usr/bin/env python3
"""
Create all Sentinel AWS resources in Floci for local testing.
Run once before running the end-to-end test harness.

Usage:
    python scripts/bootstrap_floci.py
"""

import boto3
import sys

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
DUMMY_CREDS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

KINESIS_STREAMS = [
    "sentinel-alerts",
    "sentinel-events",
    "sentinel-logs",
]

SQS_QUEUES = [
    "sentinel-validation-jobs",
    "sentinel-confirmed-incidents",
]

DYNAMODB_TABLES = [
    {
        "TableName": "sentinel-incidents",
        "KeySchema": [{"AttributeName": "incident_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "incident_id", "AttributeType": "S"},
            {"AttributeName": "severity", "AttributeType": "S"},
            {"AttributeName": "service_name", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "severity-index",
                "KeySchema": [
                    {"AttributeName": "severity", "KeyType": "HASH"},
                    {"AttributeName": "service_name", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            }
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "sentinel-validation-results",
        "KeySchema": [{"AttributeName": "alert_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "alert_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "sentinel-pr-reviews",
        "KeySchema": [{"AttributeName": "pr_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "pr_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
]


def _ok(msg):
    print(f"  \033[32m✓\033[0m  {msg}")

def _skip(msg):
    print(f"  \033[33m–\033[0m  {msg} (already exists)")

def _err(msg):
    print(f"  \033[31m✗\033[0m  {msg}")


def create_kinesis_streams():
    print("\n── Kinesis streams ──────────────────────────────")
    client = boto3.client("kinesis", **DUMMY_CREDS)
    existing = {s["StreamName"] for s in client.list_streams()["StreamNames"] if isinstance(s, str)}
    # Floci returns stream names directly
    try:
        existing = set(client.list_streams()["StreamNames"])
    except Exception:
        existing = set()

    for name in KINESIS_STREAMS:
        if name in existing:
            _skip(name)
            continue
        try:
            client.create_stream(StreamName=name, ShardCount=1)
            _ok(name)
        except client.exceptions.ResourceInUseException:
            _skip(name)
        except Exception as e:
            _err(f"{name}: {e}")


def create_sqs_queues():
    print("\n── SQS queues ───────────────────────────────────")
    client = boto3.client("sqs", **DUMMY_CREDS)
    try:
        existing_urls = client.list_queues().get("QueueUrls", [])
        existing_names = {u.split("/")[-1] for u in existing_urls}
    except Exception:
        existing_names = set()

    for name in SQS_QUEUES:
        if name in existing_names:
            _skip(name)
            continue
        try:
            client.create_queue(QueueName=name)
            _ok(name)
        except client.exceptions.QueueNameExists:
            _skip(name)
        except Exception as e:
            _err(f"{name}: {e}")


def create_dynamodb_tables():
    print("\n── DynamoDB tables ──────────────────────────────")
    client = boto3.client("dynamodb", **DUMMY_CREDS)
    try:
        existing = set(client.list_tables()["TableNames"])
    except Exception:
        existing = set()

    for spec in DYNAMODB_TABLES:
        name = spec["TableName"]
        if name in existing:
            _skip(name)
            continue
        try:
            params = {k: v for k, v in spec.items() if k != "GlobalSecondaryIndexes" or spec.get("GlobalSecondaryIndexes")}
            boto3.resource("dynamodb", **DUMMY_CREDS).create_table(**spec)
            _ok(name)
        except Exception as e:
            if "ResourceInUseException" in str(type(e)):
                _skip(name)
            else:
                _err(f"{name}: {e}")


def verify_floci():
    print("\n── Verifying Floci connection ───────────────────")
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:4566/_localstack/health", timeout=3) as r:
            _ok(f"Floci reachable (status {r.status})")
            return True
    except Exception as e:
        _err(f"Floci not reachable at localhost:4566 — is it running? ({e})")
        return False


def main():
    print("Sentinel — Floci bootstrap")
    print("=" * 50)

    if not verify_floci():
        sys.exit(1)

    create_kinesis_streams()
    create_sqs_queues()
    create_dynamodb_tables()

    print("\n" + "=" * 50)
    print("Bootstrap complete. Run the test harness next:")
    print("  python scripts/e2e_test.py")


if __name__ == "__main__":
    main()
