resource "random_password" "rds" {
  length           = 20
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_parameter_group" "mysql" {
  name   = "irap-mysql8"
  family = "mysql8.0"

  parameter {
    name         = "general_log"
    value        = "1"
    apply_method = "immediate"
  }

  parameter {
    name         = "log_output"
    value        = "TABLE"
    apply_method = "immediate"
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "irap-db-subnet-group"
  subnet_ids = module.vpc.private_subnets
}

# Security group for RDS — no inline rules (avoids circular dependency with lambda SG)
resource "aws_security_group" "rds" {
  name        = "irap-rds"
  description = "RDS MySQL - ingress from Lambda extractor"
  vpc_id      = module.vpc.vpc_id
}

# Ingress: allow Lambda extractor → RDS on port 3306
resource "aws_security_group_rule" "rds_ingress_from_lambda" {
  description              = "MySQL from Lambda extractor"
  type                     = "ingress"
  from_port                = 3306
  to_port                  = 3306
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = aws_security_group.lambda_extractor.id
}

# Egress: allow Lambda extractor → RDS on port 3306
resource "aws_security_group_rule" "lambda_egress_to_rds" {
  description              = "MySQL to RDS"
  type                     = "egress"
  from_port                = 3306
  to_port                  = 3306
  protocol                 = "tcp"
  security_group_id        = aws_security_group.lambda_extractor.id
  source_security_group_id = aws_security_group.rds.id
}

resource "aws_db_instance" "mysql" {
  identifier        = "irap-mysql"
  engine            = "mysql"
  engine_version    = "8.0"
  instance_class    = "db.t3.micro"
  allocated_storage = 20
  storage_type      = "gp2"
  storage_encrypted = true
  kms_key_id        = aws_kms_key.s3.arn

  db_name  = "irap_audit"
  username = var.rds_username
  password = random_password.rds.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  parameter_group_name   = aws_db_parameter_group.mysql.name

  backup_retention_period = 7
  skip_final_snapshot     = true
  deletion_protection     = false

  lifecycle {
    ignore_changes = [password]
  }

  depends_on = [aws_db_parameter_group.mysql]
}
