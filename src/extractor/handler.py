import csv
import io
import json
import logging
import os
from datetime import datetime, timezone

import boto3
import pymysql

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3", region_name="ap-southeast-2")
secretsmanager = boto3.client("secretsmanager", region_name="ap-southeast-2")


def _env(name: str, default: str | None = None) -> str:
    """Retrieve a required environment variable, raising clearly at call time."""
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_credentials(secret_arn: str) -> dict:
    response = secretsmanager.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def query_general_log(cursor) -> list[dict]:
    cursor.execute("""
        SELECT event_time, user_host, command_type, argument
        FROM mysql.general_log
        WHERE event_time >= NOW() - INTERVAL 7 DAY
        ORDER BY event_time ASC
    """)
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


def handler(event, context):
    bucket = _env("AUDIT_BUCKET")
    secret_arn = _env("RDS_SECRET_ARN")
    rds_endpoint = _env("RDS_ENDPOINT")
    rds_port = int(_env("RDS_PORT", "3306"))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s3_key = f"raw/{today}/user-activity.csv"

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
            rows = query_general_log(cursor)
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
        "date": today,
    }))

    return {"statusCode": 200, "rows": len(rows), "s3_key": s3_key}
