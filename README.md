# IRAP Audit Agent

Serverless pipeline on AWS that automates weekly MySQL RDS user activity extraction and IRAP PROTECTED compliance assessment using a Strands Agent on Bedrock.

> **Talk:** "Let the Agent Read the Boring Reports: AI-Powered Database Auditing for PROTECTED Compliance on AWS"

---

## Design

```
EventBridge (Sunday 14:00 UTC)
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 1 — Extractor Lambda (VPC, Python 3.12)          │
│                                                         │
│  Secrets Manager ──► RDS MySQL                          │
│  SELECT * FROM mysql.general_log WHERE last 7 days      │
│                     │                                   │
│                     ▼                                   │
│             S3: raw/YYYY-MM-DD/user-activity.csv        │
└─────────────────────────────────────────────────────────┘
         │  S3 ObjectCreated event
         ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 2 — Assessor Lambda (no VPC, Python 3.12)        │
│                                                         │
│  Strands Agent (Claude Haiku 4.5 via Bedrock)           │
│  ┌────────────────────────────────────────────────┐     │
│  │  System prompt: IRAP assessor persona          │     │
│  │  + ISM-0109 / ISM-0585 / ISM-1405 / ISM-1586  │     │
│  │                                                │     │
│  │  Tool: get_activity_data()                     │     │
│  │  (closure over S3 key — no hallucination)      │     │
│  └────────────────────────────────────────────────┘     │
│                     │                                   │
│                     ▼                                   │
│      S3: reports/YYYY-MM-DD/compliance-report.csv       │
│                     │                                   │
│                     ▼                                   │
│                SNS notification                         │
└─────────────────────────────────────────────────────────┘
```

### ISM Controls Assessed

| Control | Title |
|---|---|
| ISM-0109 | Event Log Management |
| ISM-0585 | System Access Logging |
| ISM-1405 | Database Activity Monitoring |
| ISM-1586 | Privileged Access Logging |

### Report Output

Each run produces a CSV at `reports/YYYY-MM-DD/compliance-report.csv`:

| Column | Example |
|---|---|
| `ism_control_id` | `ISM-1586` |
| `control_description` | `Privileged Access Logging` |
| `status` | `PASS` / `FAIL` / `REQUIRES_REVIEW` |
| `finding` | Formal assessor-language description |
| `evidence` | Exact log entry that triggered the finding |

---

## Repository Structure

```
├── src/
│   ├── extractor/          # Stage 1 Lambda — queries RDS, writes CSV to S3
│   │   ├── handler.py
│   │   ├── test_handler.py
│   │   └── requirements.txt
│   ├── assessor/           # Stage 2 Lambda — Strands Agent IRAP assessor
│   │   ├── handler.py
│   │   ├── system_prompt.py
│   │   ├── tools.py        # get_activity_data closure factory
│   │   ├── callback.py     # Structured CloudWatch trace logging
│   │   ├── test_handler.py
│   │   ├── test_tools.py
│   │   └── requirements.txt
│   └── seeder/             # One-shot demo data loader (invoke manually)
│       └── handler.py
├── infra/                  # Terraform — all AWS resources
│   ├── main.tf             # Provider (ap-southeast-2) + S3 backend
│   ├── vpc.tf              # VPC module, private subnets, NAT gateway
│   ├── rds.tf              # RDS MySQL instance
│   ├── lambda.tf           # Lambda functions and layers
│   ├── iam.tf              # Least-privilege IAM roles
│   ├── kms.tf              # Customer-managed KMS keys
│   ├── s3.tf               # Audit bucket (SSE-KMS, versioning, policy)
│   ├── secrets.tf          # RDS credentials in Secrets Manager
│   ├── eventbridge.tf      # Sunday cron schedule
│   ├── cloudwatch.tf       # Log groups (90-day retention)
│   └── sns.tf              # Compliance alert notifications
├── docs/
│   ├── specs/irap-audit-pipeline.md
│   └── plans/irap-audit-pipeline.md
└── .github/workflows/
    ├── ci.yml              # PR: test + terraform validate
    └── deploy.yml          # Push to master: test → terraform apply (OIDC)
```

---

## Infrastructure

All resources deploy to `ap-southeast-2` (Sydney) for IRAP PROTECTED data sovereignty.

| Resource | Detail |
|---|---|
| **Lambda 1** | VPC-attached (private subnet), 512 MB, 15 min timeout |
| **Lambda 2** | No VPC (Bedrock has no VPC endpoint in ap-southeast-2), 1024 MB, 15 min timeout |
| **Bedrock model** | `au.anthropic.claude-haiku-4-5-20251001-v1:0` (cross-region inference profile) |
| **RDS MySQL** | Private subnet, `general_log = 1`, `log_output = TABLE` |
| **S3** | SSE-KMS (CMK), versioning enabled, strict bucket policy |
| **KMS** | Two CMKs — `irap-audit-s3` and `irap-audit-secrets` |
| **Terraform state** | S3 backend (bucket configured in `infra/main.tf`) |
| **CI/CD auth** | GitHub Actions OIDC — no stored AWS keys |

---

## Quickstart

### Prerequisites

1. AWS SSO authenticated (your configured profile)
2. Create `infra/terraform.tfvars`:
   ```hcl
   alert_email = "your-email@example.com"
   ```
3. GitHub Actions secrets: `AWS_ROLE_ARN`, `ALERT_EMAIL`

### Deploy

```bash
cd infra
terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan
```

After deploy:
- Confirm the SNS email subscription in your inbox
- Update the RDS secret password via Secrets Manager

### Run Tests

```bash
# Extractor
cd src/extractor && python3 -m pytest test_handler.py -v

# Assessor
cd src/assessor && python3 -m pytest test_tools.py test_handler.py -v
```

### Invoke Manually

```bash
# Load demo data (run once after deploy)
aws lambda invoke --function-name irap-db-seeder \
  --payload '{}' --region ap-southeast-2 response.json

# Trigger extraction + assessment cycle
aws lambda invoke --function-name irap-rds-extractor \
  --payload '{}' --region ap-southeast-2 response.json

# Watch assessor trace logs
aws logs tail /aws/lambda/irap-strands-assessor \
  --since 10m --region ap-southeast-2
```

---

## Key Design Decisions

**Two decoupled Lambdas** — S3 is the durable handoff. Each stage has its own 15-minute timeout and independent CloudWatch log group.

**Lambda 2 outside VPC** — Bedrock has no VPC endpoint in ap-southeast-2. A NAT gateway in the VPC gives the assessor internet access without exposing the extractor or RDS.

**Closure-bound tool** — `make_get_activity_data(bucket, key)` closes over the S3 key before passing the tool to the agent. The LLM cannot hallucinate the path it reads from.

**No RAG** — ISM control requirement text is embedded directly in the system prompt. Simpler and sufficient for four fixed controls.

**Inference profile required** — Claude Haiku 4.5 does not support on-demand throughput directly; the `au.` cross-region inference profile (ap-southeast-2 ↔ ap-southeast-4) is required. IAM Bedrock statement uses `Resource = "*"` because cross-region profile ARNs span regions.
