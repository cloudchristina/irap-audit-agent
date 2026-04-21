resource "aws_sns_topic" "compliance_alerts" {
  name              = "irap-compliance-alerts"
  display_name      = "IRAP Compliance Alerts"
  kms_master_key_id = aws_kms_key.secrets.arn
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.compliance_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
