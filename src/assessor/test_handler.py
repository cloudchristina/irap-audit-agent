import json
from unittest.mock import MagicMock, patch


def test_parse_s3_event():
    from handler import parse_s3_event
    event = {"Records": [{"s3": {"bucket": {"name": "my-bucket"}, "object": {"key": "raw/2026-04-14/user-activity.csv"}}}]}
    bucket, key = parse_s3_event(event)
    assert bucket == "my-bucket"
    assert key == "raw/2026-04-14/user-activity.csv"


def test_extract_report_date():
    from handler import extract_report_date
    assert extract_report_date("raw/2026-04-14/user-activity.csv") == "2026-04-14"


def test_write_report_produces_csv():
    findings = [
        {"ism_control_id": "ISM-1586", "control_description": "Privileged Access Logging",
         "status": "FAIL", "finding": "Root login detected", "evidence": "2026-04-14 02:00 root@localhost Connect"},
    ]
    with patch("handler.s3") as mock_s3, \
         patch.dict("os.environ", {"AUDIT_BUCKET": "test-bucket", "SNS_TOPIC_ARN": "arn:aws:sns:x"}):
        from handler import write_report
        key = write_report(findings, "2026-04-14")

    assert key == "reports/2026-04-14/compliance-report.csv"
    call_args = mock_s3.put_object.call_args[1]
    csv_body = call_args["Body"].decode("utf-8")
    assert "ISM-1586" in csv_body
    assert "FAIL" in csv_body
