# Lambda trust policy
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── Lambda 1: Extractor ──────────────────────────────────────────────────────

resource "aws_iam_role" "extractor" {
  name               = "irap-lambda-extractor"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "extractor" {
  role = aws_iam_role.extractor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SecretsManager"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.rds_credentials.arn
      },
      {
        Sid      = "S3Write"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.audit.arn}/raw/*"
      },
      {
        Sid      = "KMSS3"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = aws_kms_key.s3.arn
      },
      {
        Sid      = "KMSSecrets"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.secrets.arn
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          aws_cloudwatch_log_group.extractor.arn,
          "${aws_cloudwatch_log_group.extractor.arn}:*"
        ]
      },
      {
        # Required for Lambda to attach/detach ENIs when running inside a VPC
        Sid      = "VPCAccess"
        Effect   = "Allow"
        Action   = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface"
        ]
        Resource = "*"
      }
    ]
  })
}

# ── Lambda 2: Assessor ───────────────────────────────────────────────────────

resource "aws_iam_role" "assessor" {
  name               = "irap-lambda-assessor"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "assessor" {
  role = aws_iam_role.assessor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "S3Read"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.audit.arn}/raw/*"
      },
      {
        # s3:GetObject covers HeadObject — required for the idempotency
        # marker check before re-running an assessment on S3 event redelivery.
        Sid      = "S3ReportsReadWrite"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.audit.arn}/reports/*"
      },
      {
        Sid      = "KMS"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.s3.arn, aws_kms_key.secrets.arn]
      },
      {
        Sid    = "Bedrock"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = "*"
      },
      {
        Sid      = "SNS"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.compliance_alerts.arn
      },
      {
        Sid    = "XRay"
        Effect = "Allow"
        Action = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          aws_cloudwatch_log_group.assessor.arn,
          "${aws_cloudwatch_log_group.assessor.arn}:*"
        ]
      }
    ]
  })
}
