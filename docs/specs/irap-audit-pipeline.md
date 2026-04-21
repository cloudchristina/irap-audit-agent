# IRAP Audit Pipeline — Design Spec

**Talk:** "Let the Agent Read the Boring Reports: AI-Powered Database Auditing for PROTECTED Compliance on AWS"  
**Region:** `ap-southeast-2` (Sydney) — all resources stay in this region for IRAP PROTECTED data sovereignty.

---

## Overview

Two-stage event-driven pipeline: a Lambda extracts MySQL RDS user activity weekly and uploads a CSV to S3. An S3 event triggers a second Lambda that runs a Strands Agent on Bedrock as an autonomous IRAP assessor, producing a compliance report CSV with evidence.

---

## Stage 1 — Extractor Lambda

**Trigger:** EventBridge cron `cron(0 14 ? * SUN *)` (Sunday 14:00 UTC = midnight AEST)  
**Runtime:** Python 3.12 | **Timeout:** 900s | **Memory:** 512 MB  
**VPC:** Same private subnet as RDS (no NAT gateway — uses S3 Gateway Endpoint and Secrets Manager Interface Endpoint)

**Flow:**
1. Fetch RDS credentials from Secrets Manager
2. Query `mysql.general_log` for the past 7 days
3. Write CSV to S3: `raw/YYYY-MM-DD/user-activity.csv`

**Columns:** `event_time`, `user_host`, `command_type`, `argument`

---

## Stage 2 — Assessor Lambda

**Trigger:** S3 event on prefix `raw/`, suffix `.csv`  
**Runtime:** Python 3.12 | **Timeout:** 900s | **Memory:** 1024 MB  
**VPC:** None (Bedrock has no VPC endpoint in ap-southeast-2 — needs internet)  
**Model:** `au.anthropic.claude-haiku-4-5-20251001-v1:0` (cross-region inference profile)

**Flow:**
1. Parse S3 event → bucket + key
2. Instantiate Strands Agent with system prompt, `get_activity_data` tool, and `trace_callback`
3. Agent calls `get_activity_data()`, reasons over records, returns JSON array of findings
4. Strip markdown fences if present, parse JSON
5. Write `reports/YYYY-MM-DD/compliance-report.csv`
6. Publish SNS notification

### Tool: `get_activity_data`

Closure factory `make_get_activity_data(bucket, key)` closes over the S3 key so the agent cannot hallucinate it. Called with no arguments.

### ISM Controls Assessed

| Control | Title |
|---|---|
| ISM-0109 | Event Log Management |
| ISM-0585 | System Access Logging |
| ISM-1405 | Database Activity Monitoring |
| ISM-1586 | Privileged Access Logging |

### Output CSV Columns

`ism_control_id`, `control_description`, `status` (PASS/FAIL/REQUIRES_REVIEW), `finding`, `evidence`

### Observability

- **CloudWatch:** structured JSON per agent step (`tool_use`, `model_response`, `final_response`, `error`) via `trace_callback(**kwargs)` — Strands SDK passes keyword args
- **X-Ray active tracing:** captures Bedrock API latency and S3 operations

---

## Infrastructure

| Resource | Detail |
|---|---|
| S3 bucket | SSE-KMS (CMK), versioning, public access blocked, bucket policy restricts to Lambda roles + SSO role wildcard |
| KMS | Two CMKs: `irap-audit-s3` (S3) and `irap-audit-secrets` (Secrets Manager, SNS) |
| Secrets Manager | RDS credentials encrypted with secrets CMK |
| VPC | Managed by `terraform-aws-modules/vpc` — private subnets for extractor, public + NAT for assessor internet access |
| IAM | Least-privilege inline policies; Bedrock uses `Resource = "*"` (required for cross-region inference profiles) |
| SNS | Email subscription for compliance notifications |
| CloudWatch Logs | 90-day retention on both Lambda log groups |
| Terraform state | S3 backend (bucket name in `infra/main.tf`) |
| CI/CD | GitHub Actions with OIDC — no stored AWS keys |

---

## Key Design Decisions

- **Two decoupled Lambdas:** S3 is the durable handoff. Each stage has its own 15-minute budget and independent logs.
- **Lambda 2 outside VPC:** Bedrock has no VPC endpoint in ap-southeast-2. NAT gateway added to VPC for assessor internet access.
- **No RAG:** ISM control text is embedded in the system prompt — simpler and sufficient for four fixed controls.
- **JSON output enforced in system prompt:** Agent returns a JSON array; Lambda parses it directly. Markdown fence stripping handles edge cases.
- **Inference profile for Haiku 4.5:** On-demand throughput not supported; must use `au.` cross-region profile routing ap-southeast-2 ↔ ap-southeast-4.
- **SSE-KMS + versioning:** Satisfies IRAP PROTECTED encryption-at-rest and audit log integrity requirements.
