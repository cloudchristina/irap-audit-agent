import csv
import io
import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from strands import Agent
from strands.models import BedrockModel

from callback import trace_callback
from system_prompt import SYSTEM_PROMPT
from tools import make_get_activity_data

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3", region_name="ap-southeast-2")
sns = boto3.client("sns", region_name="ap-southeast-2")

MODEL_ID = "au.anthropic.claude-haiku-4-5-20251001-v1:0"

REPORT_FIELDS = ["ism_control_id", "control_description", "status", "finding", "evidence"]


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_s3_event(event: dict) -> tuple[str, str]:
    record = event["Records"][0]["s3"]
    return record["bucket"]["name"], record["object"]["key"]


def extract_report_date(key: str) -> str:
    # key format: raw/YYYY-MM-DD/user-activity.csv
    parts = key.split("/")
    return parts[1] if len(parts) >= 2 else datetime.now(timezone.utc).strftime("%Y-%m-%d")


def run_assessment(bucket: str, key: str, report_date: str) -> list[dict]:
    get_activity_data = make_get_activity_data(bucket, key)

    model = BedrockModel(model_id=MODEL_ID, region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[get_activity_data],
        callback_handler=trace_callback,
    )

    task = f"Review the database activity log for the week ending {report_date}. Use get_activity_data to load the records, then produce your compliance assessment."
    response = agent(task)

    raw_text = str(response)

    # Strip markdown code fences if the model wrapped the JSON
    json_text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", json_text)
    if match:
        json_text = match.group(1).strip()

    try:
        findings = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.error(json.dumps({"event": "parse_error", "error": str(e), "raw_text_preview": raw_text[:200]}, default=str))
        raise ValueError(f"Agent returned non-JSON response: {e}") from e
    return findings


def write_report(findings: list[dict], report_date: str, bucket: str | None = None) -> str:
    bucket = bucket or _require_env("AUDIT_BUCKET")
    report_key = f"reports/{report_date}/compliance-report.csv"
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=REPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(findings)

    s3.put_object(
        Bucket=bucket,
        Key=report_key,
        Body=output.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )
    return report_key


def notify(report_key: str, findings: list[dict], report_date: str, bucket: str, sns_topic_arn: str):

    fail_count = sum(1 for f in findings if f.get("status") == "FAIL")
    sns.publish(
        TopicArn=sns_topic_arn,
        Subject=f"IRAP Compliance Report — Week ending {report_date}",
        Message=(
            f"Weekly IRAP PROTECTED compliance assessment complete.\n\n"
            f"Report: s3://{bucket}/{report_key}\n"
            f"Findings: {len(findings)} total, {fail_count} FAIL\n"
        ),
    )


def handler(event, context):
    audit_bucket = _require_env("AUDIT_BUCKET")
    sns_topic_arn = _require_env("SNS_TOPIC_ARN")

    bucket, key = parse_s3_event(event)
    if bucket != audit_bucket:
        raise ValueError(f"Event bucket {bucket!r} does not match AUDIT_BUCKET {audit_bucket!r}")
    report_date = extract_report_date(key)

    logger.info(json.dumps({"event": "assessment_start", "bucket": bucket, "key": key, "report_date": report_date}, default=str))

    findings = run_assessment(bucket, key, report_date)
    report_key = write_report(findings, report_date, bucket=audit_bucket)
    notify(report_key, findings, report_date, bucket=audit_bucket, sns_topic_arn=sns_topic_arn)

    logger.info(json.dumps({"event": "assessment_complete", "report_key": report_key, "findings_count": len(findings)}, default=str))

    return {"statusCode": 200, "report_key": report_key, "findings_count": len(findings)}
