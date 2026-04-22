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


def make_submit_findings():
    """Factory that creates a ``submit_findings`` tool plus a sink dict that captures
    the agent's final output.

    Using a factory (rather than module-level state) keeps each Lambda invocation
    isolated — critical because Lambda containers are reused across events.

    Returns ``(tool, sink)``. After ``agent(...)`` completes, read ``sink["findings"]``.
    """
    sink: dict = {"findings": None}

    @tool
    def submit_findings(findings: list[dict]) -> str:
        """Submit your final IRAP compliance findings.

        Call this exactly once, when your assessment is complete. Do not emit the
        findings as plain text in your reply — call this tool.

        Each finding object must have these keys:
          - ism_control_id (string, e.g. "ISM-1586")
          - control_description (string, short title of the control)
          - status (one of "PASS", "FAIL", "REQUIRES_REVIEW")
          - finding (string, formal assessor language)
          - evidence (string, exact log entry quoted, or "No violations found")
        """
        sink["findings"] = findings
        return f"Recorded {len(findings)} findings."

    return submit_findings, sink
