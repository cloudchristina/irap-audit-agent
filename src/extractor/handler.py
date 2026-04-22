import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import pymysql

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3", region_name="ap-southeast-2")
secretsmanager = boto3.client("secretsmanager", region_name="ap-southeast-2")

DEFAULT_WINDOW_DAYS = 7


def _env(name: str, default: str | None = None) -> str:
    """Retrieve a required environment variable, raising clearly at call time."""
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_credentials(secret_arn: str) -> dict:
    response = secretsmanager.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def resolve_window(event: dict) -> tuple[datetime, datetime]:
    """Resolve [window_start, window_end) from the EventBridge input.

    EventBridge passes ``{"window_end": "<iso8601>", "window_days": N}``.
    On retry the same ``$.time`` is re-sent, giving us a deterministic window
    (and therefore a deterministic S3 key) so put_object is idempotent.

    Falls back to NOW() only when invoked manually without input.
    """
    window_days = int(event.get("window_days", DEFAULT_WINDOW_DAYS))
    raw_end = event.get("window_end")
    if raw_end:
        # Accept both '...Z' and '+00:00' forms.
        window_end = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
    else:
        window_end = datetime.now(timezone.utc)
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    window_start = window_end - timedelta(days=window_days)
    return window_start, window_end


def query_general_log(cursor, window_start: datetime, window_end: datetime) -> list[dict]:
    cursor.execute(
        """
        SELECT event_time, user_host, command_type, argument
        FROM mysql.general_log
        WHERE event_time >= %s AND event_time < %s
        ORDER BY event_time ASC
        """,
        (window_start, window_end),
    )
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return "event_time,user_host,command_type,argument\n"
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["event_time", "user_host", "command_type", "argument"]
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({
            k: v.decode("utf-8", errors="replace") if isinstance(v, (bytes, bytearray)) else str(v)
            for k, v in row.items()
        })
    return output.getvalue()


def build_s3_key(window_end: datetime) -> str:
    """Deterministic key: same window_end → same key (idempotent on retry)."""
    return f"raw/{window_end.strftime('%Y-%m-%d')}/user-activity.csv"


def handler(event, context):
    bucket = _env("AUDIT_BUCKET")
    secret_arn = _env("RDS_SECRET_ARN")
    rds_endpoint = _env("RDS_ENDPOINT")
    rds_port = int(_env("RDS_PORT", "3306"))

    window_start, window_end = resolve_window(event or {})
    s3_key = build_s3_key(window_end)

    logger.info(json.dumps({
        "event": "extraction_start",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "s3_key": s3_key,
    }))

    creds = get_credentials(secret_arn)
    conn = pymysql.connect(
        host=rds_endpoint,
        port=rds_port,
        user=creds["username"],
        password=creds["password"],
        connect_timeout=10,
        ssl={"ca": "/etc/ssl/certs/ca-bundle.crt"},
    )

    try:
        with conn.cursor() as cursor:
            rows = query_general_log(cursor, window_start, window_end)
    finally:
        conn.close()

    csv_content = rows_to_csv(rows)

    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=csv_content.encode("utf-8"),
        ContentType="text/csv",
    )

    logger.info(json.dumps({
        "event": "extraction_complete",
        "row_count": len(rows),
        "s3_key": s3_key,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }))

    return {
        "statusCode": 200,
        "rows": len(rows),
        "s3_key": s3_key,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
    }
