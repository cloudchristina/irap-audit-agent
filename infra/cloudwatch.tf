resource "aws_cloudwatch_log_group" "extractor" {
  name              = "/aws/lambda/irap-rds-extractor"
  retention_in_days = 90
  kms_key_id        = aws_kms_key.s3.arn
}

resource "aws_cloudwatch_log_group" "assessor" {
  name              = "/aws/lambda/irap-strands-assessor"
  retention_in_days = 90
  kms_key_id        = aws_kms_key.s3.arn
}
