# IRAP Audit Pipeline — Implementation Plan

**Goal:** Two-stage serverless pipeline on AWS. Stage 1 Lambda extracts MySQL RDS user activity weekly and writes a raw CSV to S3. Stage 2 Lambda runs a Strands Agent on Bedrock as an IRAP assessor, producing a compliance report CSV and SNS notification.

**Stack:** Python 3.12, AWS Lambda, Amazon Bedrock (Claude Haiku 4.5), Strands Agents SDK, RDS MySQL, S3, Secrets Manager, EventBridge, SNS, CloudWatch, X-Ray, Terraform

**See spec:** `docs/specs/irap-audit-pipeline.md`

---

## Prerequisites

- Enable Bedrock model access: `anthropic.claude-haiku-4-5-20251001-v1:0` in ap-southeast-2 console
- RDS parameter group: `general_log = 1`, `log_output = TABLE`
- Create `infra/terraform.tfvars` (not committed): `alert_email`
- After deploy: confirm SNS email subscription

---

## File Map

```
serverless-meetup/
├── infra/
│   ├── main.tf              # Provider + S3 backend
│   ├── variables.tf         # alert_email
│   ├── outputs.tf
│   ├── kms.tf               # Two CMKs: s3, secrets
│   ├── s3.tf                # Bucket, SSE-KMS, versioning, policy, S3 notification
│   ├── secrets.tf           # RDS credentials secret
│   ├── vpc.tf               # VPC module, private subnets, NAT gateway, security groups
│   ├── iam.tf               # Extractor + assessor roles and inline policies
│   ├── lambda.tf            # Layers, seeder, extractor, assessor functions
│   ├── rds.tf               # RDS MySQL instance
│   ├── eventbridge.tf       # Sunday cron + target
│   ├── cloudwatch.tf        # Log groups (90-day retention)
│   └── sns.tf               # Topic + email subscription
│
├── src/
│   ├── extractor/
│   │   ├── handler.py       # Secrets Manager → RDS → CSV → S3
│   │   ├── test_handler.py
│   │   └── requirements.txt # pymysql
│   │
│   ├── assessor/
│   │   ├── handler.py       # S3 event → Strands Agent → CSV → S3 → SNS
│   │   ├── system_prompt.py # IRAP persona + ISM-0109/0585/1405/1586 text
│   │   ├── tools.py         # make_get_activity_data closure factory
│   │   ├── callback.py      # trace_callback(**kwargs) → CloudWatch structured logs
│   │   ├── test_handler.py
│   │   ├── test_tools.py
│   │   └── requirements.txt # strands-agents
│   │
│   └── seeder/
│       └── handler.py       # One-shot demo data loader (invoke manually)
│
└── .github/workflows/
    ├── ci.yml               # PR checks: test-extractor, test-assessor, terraform-validate
    └── deploy.yml           # Push to master: tests → terraform apply (OIDC auth)
```

---

## Commands

```bash
# Tests
cd src/extractor && python3 -m pytest test_handler.py -v
cd src/assessor && python3 -m pytest test_tools.py test_handler.py -v

# Deploy
cd infra
terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan

# Invoke seeder (demo data)
aws lambda invoke --function-name irap-db-seeder \
  --payload '{}' --region ap-southeast-2 response.json

# Invoke extractor manually
aws lambda invoke --function-name irap-rds-extractor \
  --payload '{}' --region ap-southeast-2 response.json

# Watch assessor logs
aws logs tail /aws/lambda/irap-strands-assessor --since 10m --region ap-southeast-2
```

---

## Deployment Notes

- Terraform state: S3 backend (bucket name in `infra/main.tf`), key `irap-audit/terraform.tfstate`
- Bedrock inference profile `au.anthropic.claude-haiku-4-5-20251001-v1:0` routes across ap-southeast-2 and ap-southeast-4 — IAM Bedrock statement uses `Resource = "*"` (required for cross-region profiles)
- GitHub Actions OIDC: secrets required: `AWS_ROLE_ARN`, `ALERT_EMAIL`
