# ── Lambda Layer: pymysql ────────────────────────────────────────────────────

resource "null_resource" "extractor_layer_build" {
  triggers = {
    requirements = filemd5("${path.module}/../src/extractor/requirements.txt")
  }
  provisioner "local-exec" {
    command = <<EOT
      mkdir -p ${path.module}/layer_builds/extractor/python
      pip install -r ${path.module}/../src/extractor/requirements.txt \
        -t ${path.module}/layer_builds/extractor/python \
        --platform manylinux2014_x86_64 --only-binary=:all: --quiet
    EOT
  }
}

data "archive_file" "extractor_layer" {
  type        = "zip"
  source_dir  = "${path.module}/layer_builds/extractor"
  output_path = "${path.module}/layer_builds/extractor_layer.zip"
  depends_on  = [null_resource.extractor_layer_build]
}

resource "aws_lambda_layer_version" "pymysql" {
  filename            = data.archive_file.extractor_layer.output_path
  layer_name          = "pymysql"
  compatible_runtimes = ["python3.12"]
  source_code_hash    = data.archive_file.extractor_layer.output_base64sha256
  skip_destroy        = true
}

# ── Lambda Layer: strands-agents ────────────────────────────────────────────

resource "null_resource" "assessor_layer_build" {
  triggers = {
    requirements = filemd5("${path.module}/../src/assessor/requirements.txt")
  }
  provisioner "local-exec" {
    command = <<EOT
      mkdir -p ${path.module}/layer_builds/assessor/python
      pip install -r ${path.module}/../src/assessor/requirements.txt \
        -t ${path.module}/layer_builds/assessor/python \
        --platform manylinux2014_x86_64 --only-binary=:all: --quiet
    EOT
  }
}

data "archive_file" "assessor_layer" {
  type        = "zip"
  source_dir  = "${path.module}/layer_builds/assessor"
  output_path = "${path.module}/layer_builds/assessor_layer.zip"
  depends_on  = [null_resource.assessor_layer_build]
}

resource "aws_lambda_layer_version" "strands_agents" {
  filename            = data.archive_file.assessor_layer.output_path
  layer_name          = "strands-agents"
  compatible_runtimes = ["python3.12"]
  source_code_hash    = data.archive_file.assessor_layer.output_base64sha256
  skip_destroy        = true
}

# ── Lambda 0: Seeder (one-shot demo data loader, invoke manually) ────────────

data "archive_file" "seeder" {
  type        = "zip"
  source_dir  = "${path.module}/../src/seeder"
  output_path = "${path.module}/builds/seeder.zip"
}

resource "aws_lambda_function" "seeder" {
  function_name    = "irap-db-seeder"
  filename         = data.archive_file.seeder.output_path
  source_code_hash = data.archive_file.seeder.output_base64sha256
  role             = aws_iam_role.extractor.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  layers           = [aws_lambda_layer_version.pymysql.arn]

  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.lambda_extractor.id]
  }

  environment {
    variables = {
      RDS_SECRET_ARN = aws_secretsmanager_secret.rds_credentials.arn
      RDS_ENDPOINT   = aws_db_instance.mysql.address
      RDS_PORT       = tostring(aws_db_instance.mysql.port)
    }
  }

  depends_on = [aws_cloudwatch_log_group.extractor]
}

# ── Lambda 1: Extractor ──────────────────────────────────────────────────────

data "archive_file" "extractor" {
  type        = "zip"
  source_dir  = "${path.module}/../src/extractor"
  output_path = "${path.module}/builds/extractor.zip"
  excludes    = ["test_*.py", "requirements.txt"]
}

resource "aws_lambda_function" "extractor" {
  function_name    = "irap-rds-extractor"
  filename         = data.archive_file.extractor.output_path
  source_code_hash = data.archive_file.extractor.output_base64sha256
  role             = aws_iam_role.extractor.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 512
  layers           = [aws_lambda_layer_version.pymysql.arn]

  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.lambda_extractor.id]
  }

  environment {
    variables = {
      AUDIT_BUCKET   = aws_s3_bucket.audit.bucket
      RDS_SECRET_ARN = aws_secretsmanager_secret.rds_credentials.arn
      RDS_ENDPOINT   = aws_db_instance.mysql.address
      RDS_PORT       = tostring(aws_db_instance.mysql.port)
    }
  }

  depends_on = [aws_cloudwatch_log_group.extractor]
}

# ── Lambda 2: Assessor ───────────────────────────────────────────────────────

data "archive_file" "assessor" {
  type        = "zip"
  source_dir  = "${path.module}/../src/assessor"
  output_path = "${path.module}/builds/assessor.zip"
  excludes    = ["test_*.py", "requirements.txt"]
}

resource "aws_lambda_function" "assessor" {
  function_name    = "irap-strands-assessor"
  filename         = data.archive_file.assessor.output_path
  source_code_hash = data.archive_file.assessor.output_base64sha256
  role             = aws_iam_role.assessor.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 1024
  layers           = [aws_lambda_layer_version.strands_agents.arn]

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      AUDIT_BUCKET  = aws_s3_bucket.audit.bucket
      SNS_TOPIC_ARN = aws_sns_topic.compliance_alerts.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.assessor]
}

# Allow S3 to invoke Lambda 2
resource "aws_lambda_permission" "s3_invoke_assessor" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.assessor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.audit.arn
}
