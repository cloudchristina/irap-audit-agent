import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


def test_parse_s3_event_extracts_version_id():
    from handler import parse_s3_event
    event = {"Records": [{"s3": {
        "bucket": {"name": "my-bucket"},
        "object": {"key": "raw/2026-04-14/user-activity.csv", "versionId": "abc123"},
    }}]}
    bucket, key, version_id = parse_s3_event(event)
    assert bucket == "my-bucket"
    assert key == "raw/2026-04-14/user-activity.csv"
    assert version_id == "abc123"


def test_parse_s3_event_version_id_optional():
    from handler import parse_s3_event
    event = {"Records": [{"s3": {
        "bucket": {"name": "my-bucket"},
        "object": {"key": "raw/2026-04-14/user-activity.csv"},
    }}]}
    _, _, version_id = parse_s3_event(event)
    assert version_id is None


def test_extract_report_date():
    from handler import extract_report_date
    assert extract_report_date("raw/2026-04-14/user-activity.csv") == "2026-04-14"


def test_marker_key_includes_version_id():
    from handler import marker_key
    assert marker_key("2026-04-14", "abc123") == "reports/2026-04-14/.processed-abc123"


def test_marker_key_falls_back_when_version_id_absent():
    from handler import marker_key
    assert marker_key("2026-04-14", None) == "reports/2026-04-14/.processed-noversion"


def test_already_processed_returns_true_when_head_succeeds():
    from handler import already_processed
    with patch("handler.s3") as mock_s3:
        mock_s3.head_object.return_value = {"ContentLength": 0}
        assert already_processed("b", "reports/2026-04-14/.processed-v1") is True


def test_already_processed_returns_false_when_not_found():
    from handler import already_processed
    with patch("handler.s3") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        assert already_processed("b", "reports/2026-04-14/.processed-v1") is False


def test_already_processed_reraises_other_client_errors():
    import pytest
    from handler import already_processed
    with patch("handler.s3") as mock_s3:
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "HeadObject"
        )
        with pytest.raises(ClientError):
            already_processed("b", "marker")


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


def test_handler_skips_on_redelivery():
    """If the idempotency marker exists, we must not re-invoke Bedrock or re-send SNS."""
    event = {"Records": [{"s3": {
        "bucket": {"name": "test-bucket"},
        "object": {"key": "raw/2026-04-14/user-activity.csv", "versionId": "v1"},
    }}]}
    with patch("handler.s3") as mock_s3, \
         patch("handler.sns") as mock_sns, \
         patch("handler.run_assessment") as mock_run, \
         patch.dict("os.environ", {"AUDIT_BUCKET": "test-bucket", "SNS_TOPIC_ARN": "arn:aws:sns:x"}):
        mock_s3.head_object.return_value = {"ContentLength": 0}  # marker exists

        from handler import handler
        result = handler(event, None)

    assert result["skipped"] is True
    mock_run.assert_not_called()
    mock_sns.publish.assert_not_called()


def test_handler_writes_marker_before_bedrock():
    """Marker must be written before run_assessment so a crash after Bedrock cannot
    cause duplicate SNS alerts on re-delivery. A phantom marker is recoverable;
    a duplicate compliance alert is not."""
    event = {"Records": [{"s3": {
        "bucket": {"name": "test-bucket"},
        "object": {"key": "raw/2026-04-14/user-activity.csv", "versionId": "v2"},
    }}]}
    call_order = []

    with patch("handler.s3") as mock_s3, \
         patch("handler.sns"), \
         patch("handler.run_assessment") as mock_run, \
         patch("handler.write_report", return_value="reports/2026-04-14/compliance-report.csv"), \
         patch("handler.notify"), \
         patch.dict("os.environ", {"AUDIT_BUCKET": "test-bucket", "SNS_TOPIC_ARN": "arn:aws:sns:x"}):
        from botocore.exceptions import ClientError
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        mock_s3.put_object.side_effect = lambda **_: call_order.append("write_marker")
        mock_run.side_effect = lambda *_: call_order.append("run_assessment") or []

        from handler import handler
        handler(event, None)

    assert call_order.index("write_marker") < call_order.index("run_assessment")
