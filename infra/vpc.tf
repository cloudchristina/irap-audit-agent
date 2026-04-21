module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = "irap-audit"
  cidr = "10.0.0.0/24"

  azs             = ["${data.aws_region.current.id}a", "${data.aws_region.current.id}b"]
  private_subnets = ["10.0.0.0/28", "10.0.0.16/28"]
  public_subnets  = ["10.0.0.32/28", "10.0.0.48/28"]

  enable_nat_gateway     = true
  single_nat_gateway     = true   # one NAT GW is enough for a demo
  enable_vpn_gateway     = false

  enable_dns_hostnames = true
  enable_dns_support   = true
}

# ── Security group for Lambda 1 — rules managed via aws_security_group_rule ──

resource "aws_security_group" "lambda_extractor" {
  name        = "irap-lambda-extractor"
  description = "Lambda 1 extractor - outbound to RDS and VPC endpoints"
  vpc_id      = module.vpc.vpc_id
}

resource "aws_security_group_rule" "lambda_egress_https" {
  description       = "HTTPS to VPC endpoints"
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.lambda_extractor.id
  cidr_blocks       = ["0.0.0.0/0"]
}

# The Secrets Manager Interface endpoint uses this same SG.
# Allow inbound 443 from Lambda (self-referencing) so the endpoint ENI accepts traffic.
resource "aws_security_group_rule" "lambda_ingress_https_self" {
  description              = "Allow HTTPS from Lambda to Secrets Manager endpoint ENI"
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.lambda_extractor.id
  source_security_group_id = aws_security_group.lambda_extractor.id
}

# ── S3 Gateway Endpoint ──────────────────────────────────────────────────────

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
}

# ── Secrets Manager Interface Endpoint ──────────────────────────────────────

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.id}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.lambda_extractor.id]
  private_dns_enabled = true
}
