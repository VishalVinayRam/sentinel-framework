import json
from typing import Optional

import boto3

from sentinel.providers.base.cloud import BaseCloudProvider


class AWSProvider(BaseCloudProvider):
    """AWS cloud provider. Works with real AWS and Floci (local emulator)."""

    def __init__(self, region: str = "us-east-1", endpoint_url: Optional[str] = None):
        self._region = region
        self._endpoint = endpoint_url
        self._kwargs = dict(region_name=region)
        if endpoint_url:
            self._kwargs["endpoint_url"] = endpoint_url
            self._kwargs["aws_access_key_id"] = "test"
            self._kwargs["aws_secret_access_key"] = "test"

    def _kinesis(self):
        return boto3.client("kinesis", **self._kwargs)

    def _sqs(self):
        return boto3.client("sqs", **self._kwargs)

    def _dynamodb(self):
        return boto3.resource("dynamodb", **self._kwargs)

    def _s3(self):
        return boto3.client("s3", **self._kwargs)

    def _secrets(self):
        return boto3.client("secretsmanager", **self._kwargs)

    # --- Event Streaming ---

    def publish_event(self, stream: str, data: dict, partition_key: str) -> None:
        self._kinesis().put_record(
            StreamName=stream,
            Data=json.dumps(data),
            PartitionKey=partition_key,
        )

    def publish_batch(self, stream: str, records: list[dict]) -> None:
        entries = [
            {"Data": json.dumps(r), "PartitionKey": r.get("aggregate_id", "default")}
            for r in records
        ]
        self._kinesis().put_records(StreamName=stream, Records=entries)

    # --- Queue ---

    def enqueue(self, queue: str, message: dict, delay_seconds: int = 0) -> str:
        resp = self._sqs().send_message(
            QueueUrl=queue,
            MessageBody=json.dumps(message),
            DelaySeconds=delay_seconds,
        )
        return resp["MessageId"]

    def dequeue(self, queue: str, max_messages: int = 10) -> list[dict]:
        resp = self._sqs().receive_message(
            QueueUrl=queue,
            MaxNumberOfMessages=min(max_messages, 10),
            WaitTimeSeconds=20,
        )
        return resp.get("Messages", [])

    def delete_message(self, queue: str, receipt: str) -> None:
        self._sqs().delete_message(QueueUrl=queue, ReceiptHandle=receipt)

    # --- Document Store ---

    def put_item(self, table: str, item: dict) -> None:
        self._dynamodb().Table(table).put_item(Item=item)

    def get_item(self, table: str, key: dict) -> Optional[dict]:
        resp = self._dynamodb().Table(table).get_item(Key=key)
        return resp.get("Item")

    def update_item(self, table: str, key: dict, updates: dict) -> None:
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
        names = {f"#{k}": k for k in updates}
        values = {f":{k}": v for k, v in updates.items()}
        self._dynamodb().Table(table).update_item(
            Key=key,
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def query_items(self, table: str, index: str, key_condition: dict) -> list[dict]:
        from boto3.dynamodb.conditions import Key as DynamoKey
        cond = None
        for k, v in key_condition.items():
            c = DynamoKey(k).eq(v)
            cond = c if cond is None else cond & c
        resp = self._dynamodb().Table(table).query(IndexName=index, KeyConditionExpression=cond)
        return resp.get("Items", [])

    # --- Object Storage ---

    def upload_file(self, bucket: str, key: str, data: bytes) -> None:
        self._s3().put_object(Bucket=bucket, Key=key, Body=data)

    def download_file(self, bucket: str, key: str) -> bytes:
        resp = self._s3().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()

    def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        resp = self._s3().list_objects_v2(Bucket=bucket, Prefix=prefix)
        return [o["Key"] for o in resp.get("Contents", [])]

    # --- Secrets ---

    def get_secret(self, name: str) -> str:
        resp = self._secrets().get_secret_value(SecretId=name)
        return resp.get("SecretString", "")
