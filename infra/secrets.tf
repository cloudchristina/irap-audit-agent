resource "aws_secretsmanager_secret" "rds_credentials" {
  name       = "irap-audit/rds-credentials"
  kms_key_id = aws_kms_key.secrets.arn
}

resource "aws_secretsmanager_secret_version" "rds_credentials" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = var.rds_username
    password = random_password.rds.result
    host     = aws_db_instance.mysql.address
    port     = aws_db_instance.mysql.port
  })
}
