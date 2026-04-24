import boto3
import os

FLOCI_ENDPOINT = os.environ.get("FLOCI_ENDPOINT", "http://localhost:4566")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

_kwargs = dict(
    endpoint_url=FLOCI_ENDPOINT,
    region_name=AWS_REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)


def dynamodb():
    return boto3.resource("dynamodb", **_kwargs)


def kinesis():
    return boto3.client("kinesis", **_kwargs)


def sqs():
    return boto3.client("sqs", **_kwargs)


def s3():
    return boto3.client("s3", **_kwargs)


def secrets_manager():
    return boto3.client("secretsmanager", **_kwargs)
