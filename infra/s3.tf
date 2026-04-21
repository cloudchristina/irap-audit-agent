resource "aws_s3_bucket" "audit" {
  bucket = "irap-audit-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.id}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id

  depends_on = [aws_s3_bucket_public_access_block.audit]

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.audit.id

  depends_on = [aws_s3_bucket_public_access_block.audit]

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "audit" {
  bucket = aws_s3_bucket.audit.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyAllExceptLambdasAndTerraform"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource  = [aws_s3_bucket.audit.arn, "${aws_s3_bucket.audit.arn}/*"]
        Condition = {
          StringNotLike = {
            "aws:PrincipalArn" = [
              aws_iam_role.extractor.arn,
              aws_iam_role.assessor.arn,
              "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
              "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/sso.amazonaws.com/*",
              "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.ci_role_name}",
            ]
          }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.audit]
}

resource "aws_s3_bucket_notification" "trigger_assessor" {
  bucket = aws_s3_bucket.audit.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.assessor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/"
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.s3_invoke_assessor]
}
