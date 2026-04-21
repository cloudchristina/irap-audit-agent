# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Two-stage serverless pipeline on AWS for IRAP PROTECTED database compliance auditing:

1. **Stage 1 — Extractor Lambda** (`src/extractor/`): triggered by EventBridge cron (Sunday 14:00 UTC), queries `mysql.general_log` on RDS MySQL, writes weekly CSV to S3 under `raw/YYYY-MM-DD/`
2. **Stage 2 — Strands Agent Assessor Lambda** (`src/assessor/`): triggered by S3 event on `raw/*.csv`, runs a Bedrock-backed Strands Agent as an IRAP assessor, writes compliance report CSV to `reports/YYYY-MM-DD/`, publishes SNS notification

All AWS resources deploy to `ap-southeast-2` (Sydney) for IRAP PROTECTED data sovereignty.

## Commands

```bash
# Terraform
cd infra
terraform init
terraform validate
terraform plan -out=plan.tfplan
terraform apply plan.tfplan

# Extractor tests
cd src/extractor && python3 -m pytest test_handler.py -v

# Assessor tests
cd src/assessor && python3 -m pytest test_tools.py test_handler.py -v
```

## Architecture

### Key Design Decisions
- **Lambda 1 is VPC-attached** (same subnet as RDS). Accesses S3 via Gateway endpoint and Secrets Manager via Interface endpoint — no NAT gateway needed.
- **Lambda 2 is outside VPC** — Bedrock has no VPC endpoint in ap-southeast-2; Lambda 2 needs public internet access.
- **Strands Agent `get_activity_data` tool uses a closure factory** — `make_get_activity_data(bucket, key)` closes over the S3 key so the LLM cannot hallucinate it. The agent calls the tool with no arguments.
- **Agent output is a JSON array** (enforced in system prompt). `handler.py` strips markdown code fences then parses with `json.loads`, wrapped in a try/except with structured error logging.
- **SSE-KMS** on S3, Secrets Manager, SNS, and CloudWatch log groups — all with customer-managed keys in `kms.tf`.

### ISM Controls Assessed
ISM-0109, ISM-0585, ISM-1405, ISM-1586 — full requirement text is embedded in `src/assessor/system_prompt.py`.

## Deployment Prerequisites

Before `terraform apply`:
1. Bedrock model access is automatic on first invocation for `au.anthropic.claude-haiku-4-5-20251001-v1:0` — no manual enablement needed. A user with AWS Marketplace permissions must invoke first if the account has never used this model.
2. RDS parameter group: `general_log = 1`, `log_output = TABLE`
3. Create `infra/terraform.tfvars` from `terraform.tfvars.example` — only `alert_email` is required (VPC and subnets are managed by the vpc module)
4. After deploy: confirm SNS email subscription and update the RDS secret password via Secrets Manager

```bash
# Get RDS credentials from Secrets Manager
SECRET=$(aws secretsmanager get-secret-value \
  --secret-id irap-audit/rds-credentials \
  --region ap-southeast-2 --query SecretString --output text)
HOST=$(echo "$SECRET" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['host'])")
USER=$(echo "$SECRET" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['username'])")
PASS=$(echo "$SECRET" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['password'])")
mysql -h "$HOST" -u "$USER" -p"$PASS" --ssl-ca /tmp/rds-bundle.pem
```
