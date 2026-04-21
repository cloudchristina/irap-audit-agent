import csv
import io

import boto3
from strands import tool

s3 = boto3.client("s3", region_name="ap-southeast-2")


def make_get_activity_data(bucket: str, key: str):
    """Factory that closes over bucket/key so the agent calls the tool with no arguments."""

    @tool
    def get_activity_data() -> list[dict]:
        """
        Read the raw user activity CSV from S3 and return rows as a list of dicts.
        Each dict has keys: event_time, user_host, command_type, argument.
        """
        obj = s3.get_object(Bucket=bucket, Key=key)
        content = obj["Body"].read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        return list(reader)

    return get_activity_data
