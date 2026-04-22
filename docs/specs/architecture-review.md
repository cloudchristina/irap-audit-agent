---
title: IRAP Audit Agent — Architecture Review
date: 2026-04-22
reviewer: system-design pass (Claude)
scope: existing two-stage pipeline as of current `main`
---

# IRAP Audit Agent — Architecture Review

> **Status update (2026-04-22):** The "quick wins" from §4 have been
> implemented in a follow-up change. Specifically:
>
> - **TLS enforcement** on the audit bucket (§4.12) — `DenyInsecureTransport` added to `s3.tf`.
> - **Dedicated logs CMK** (§4.11) — new `aws_kms_key.logs`, scoped via `kms:EncryptionContext:aws:logs:arn` to IRAP log groups; s3/secrets CMKs no longer grant to CloudWatch Logs.
> - **Deterministic extraction window** (§4.3) — EventBridge passes `$.time` via `input_transformer`; extractor reads `window_end`/`window_days` and uses a parameterised SQL `BETWEEN`. S3 key is now keyed on `window_end`, making `put_object` idempotent under EventBridge retry.
> - **Assessor idempotency guard** (§4.7) — per-source `versionId` marker file; re-delivered S3 events skip Bedrock and SNS instead of re-notifying on-call.
> - **Structured agent output** (§4.5) — regex JSON parse deleted; agent now calls a `submit_findings` tool whose payload is captured into a closure sink. The system prompt was updated to match.
>
> Items still outstanding from §4: #1 (`mysql.general_log` source), #2 (tamper-evidence / Object Lock), #4 (agent over-reads full CSV), #6 (no DLQ / queue), §4.8 Bedrock-boundary question, and all of §7 (monitoring).


## 1. Requirements (as inferred)

**Functional**
- Capture MySQL RDS user activity weekly and persist to S3.
- Autonomously assess that activity against four ISM controls (0109, 0585, 1405, 1586).
- Produce an assessor-ready compliance report and notify a human.

**Non-functional**
- Data sovereignty: all processing in `ap-southeast-2` (IRAP PROTECTED).
- Encryption at rest with customer-managed KMS keys; TLS in transit.
- Scheduled, batch (weekly). Latency is not a hot path.
- Minimise standing infrastructure (serverless-first).

**Constraints**
- Bedrock has no VPC endpoint in ap-southeast-2 → Assessor must reach the public service endpoint.
- Single-author demo scope; Terraform-managed; Python 3.12 Lambdas.
- `general_log = 1, log_output = TABLE` is the current audit source.

## 2. Current architecture

```
                           ┌──────────────────────────┐
                           │  EventBridge (cron SUN)  │
                           └────────────┬─────────────┘
                                        │ invoke
                                        ▼
┌─────────────────── VPC (10.0.0.0/24) ──────────────────┐
│                                                         │
│   ┌──────────────────┐   mysql://    ┌───────────────┐  │
│   │ Lambda: extractor│◀────3306────▶│ RDS MySQL 8.0 │  │
│   │  (pymysql layer) │               │ general_log   │  │
│   └───────┬──────────┘               └───────────────┘  │
│           │  s3:PutObject (Gateway endpoint)            │
│           │  secretsmanager:GetSecretValue (Interface)  │
└───────────┼──────────────────────────────────────────────┘
            ▼
    ┌──────────────────┐
    │ S3  raw/DATE/*   │────s3:ObjectCreated:*────┐
    │ SSE-KMS, v-ing   │  (prefix=raw/ suffix=csv)│
    └──────────────────┘                          ▼
                                    ┌──────────────────────────┐
                                    │ Lambda: assessor (no VPC)│
                                    │ Strands Agent ─▶ Bedrock │
                                    │ au.anthropic.haiku-4-5   │
                                    └──────────┬───────────────┘
                                               │ write reports/DATE/*
                                               ▼
                                        ┌──────────────┐
                                        │  S3 reports/ │
                                        └──────┬───────┘
                                               ▼
                                        ┌──────────────┐
                                        │ SNS → email  │
                                        └──────────────┘
```

Cross-cutting: two CMKs (`alias/irap-audit-s3`, `alias/irap-audit-secrets`), CloudWatch log groups (90d), X-Ray on the assessor.

## 3. What's working well

- **Clean stage separation.** Stage 1 is VPC-attached (needs RDS, doesn't need Bedrock). Stage 2 is outside the VPC (needs Bedrock, doesn't need RDS). No pointless NAT traffic from the assessor; no pointless VPC endpoint for Bedrock.
- **Closure-factory tool pattern.** `make_get_activity_data(bucket, key)` binds the S3 target at invocation time. The model cannot hallucinate or inject a different S3 key — the single strongest design choice in the repo.
- **Event fan-out by prefix filter.** S3 notification filters on `raw/` + `.csv`, so the assessor writing back to `reports/` can't cause a recursive trigger.
- **Least-privilege IAM per Lambda.** Extractor has `s3:PutObject` only on `raw/*`; assessor has `s3:GetObject` on `raw/*` and `s3:PutObject` on `reports/*`. No wildcard bucket access.
- **Bucket policy is deny-by-default.** `DenyAllExceptLambdasAndTerraform` blocks anything that isn't one of the two Lambda roles, root, SSO, or CI.
- **CMKs, rotation, and deletion window.** SSE-KMS on the bucket, KMS on Secrets Manager and on the SNS topic, and on log groups. Key rotation enabled.
- **Versioning on.** Recovery path exists for accidental overwrite of `raw/` or `reports/` keys.

## 4. Risks, in priority order

### P0 — Compliance / correctness

1. **`mysql.general_log` is the wrong audit source for PROTECTED.** It captures every statement (including Lambda's own connection handshakes), is single-threaded on write, degrades RDS performance under load, and rotates on a table-size or age basis you don't control. ISM-1405 is really asking for the **MySQL Audit Plugin** (or RDS IAM-auth CloudTrail events, or `MARIADB_AUDIT_PLUGIN` equivalent) — these give you structured connect/disconnect/DDL/DML events without statement-level noise. `general_log` will also silently drop entries if rotated before extraction. Today's design could PASS an ISM-1405 check on paper while producing an incomplete audit trail in practice.
   *Fix:* migrate to Audit Plugin → Kinesis Firehose → S3, or at minimum keep `general_log` as a dev demo only.

2. **No tamper-evidence on the report or the raw extract.** Versioning helps against accidental overwrite, not against an authorised principal rewriting history. For an **assessor-ready** artefact, S3 Object Lock (compliance mode) on both `raw/` and `reports/` is the expected posture. Also no SHA-256 / detached signature on the report CSV.
   *Fix:* enable Object Lock at bucket creation (cannot be added retroactively), set a retention policy aligned to ISM log-retention (7 years is typical), emit a sidecar `.sha256` or sign with KMS `Sign`.

3. **Extractor window is wall-clock, not idempotent.** `WHERE event_time >= NOW() - INTERVAL 7 DAY` re-evaluates on every invocation. Re-run the Lambda on Monday 09:00 and you get a different window than Sunday 14:00. If EventBridge retries (it can — async invocation semantics), you get two different CSVs under the same `raw/DATE/user-activity.csv` key — S3 `put_object` overwrites, so the second run wins, silently.
   *Fix:* pass `{"window_start":"...","window_end":"..."}` via the EventBridge `input` block and key the S3 object on the window, not on "today".

### P1 — Scale and operational

4. **The agent consumes the full CSV into context.** `get_activity_data()` returns every row as a list of dicts. A moderately busy PROTECTED DB with `general_log = 1` easily generates 100k–1M statements/week. That will blow past Haiku's context window and inflate Bedrock spend by orders of magnitude. On a small dev DB this works; the failure will appear the first time real traffic hits it.
   *Fix:* replace the single tool with a small toolkit — `list_users()`, `query_by_control(control_id)`, `sample_anomalies(type)`, `count_by(field)` — that return aggregates, not rows. Have the agent reason over summaries and only pull raw lines as evidence for a specific finding.

5. **Brittle JSON parsing off a free-text response.** `re.search(r"\`\`\`(?:json)?\s*([\s\S]+?)\`\`\`", ...)` grabs the first fenced block. If the model emits prose then a fenced block then more prose, the fence is picked up; if it emits two fences (rare but possible on refusals/retries), the second is ignored. One model drift and the pipeline starts failing loudly — better to fail loudly than silently, but this is a known antipattern.
   *Fix:* expose a `submit_findings(findings: list[Finding])` tool and let the agent call it. The tool call is schema-validated. The final text response becomes irrelevant.

6. **No DLQ, no retry path for the assessor.** S3 event notifications are async — Lambda retries twice and drops. If Bedrock is throttled for 10 minutes, you lose the week. Nothing alerts on a skipped week.
   *Fix:* S3 → SQS → Lambda (with SQS DLQ after N receives), or S3 → EventBridge → Step Functions with explicit retry/catch. Add a CloudWatch alarm on "no object written to `reports/` in the last 8 days".

7. **Double-notify on event redelivery.** `handler.py` has no idempotency guard. S3 can redeliver a notification; the assessor will re-run Bedrock, overwrite the report (fine), and re-send the SNS email (not fine for the on-call).
   *Fix:* check `HeadObject` on `reports/DATE/compliance-report.csv` before running, or write an idempotency marker to DynamoDB keyed on the input S3 `version-id`.

### P2 — Defence in depth

8. **PROTECTED data crosses the public Bedrock endpoint.** The assessor runs outside the VPC and sends `general_log` rows (potentially containing PII or privileged statements) to `bedrock-runtime.ap-southeast-2.amazonaws.com` over the public internet. This is likely fine under your IRAP boundary — AWS Bedrock in ap-southeast-2 is within the IRAP-assessed envelope for a subset of services — but it deserves to be explicit in your SSP (System Security Plan). Worth confirming with your IRAP assessor whether Bedrock + your specific model is in scope at PROTECTED. If not, you need either an in-boundary model or redaction before prompt construction.

9. **Single NAT gateway, two AZs.** `single_nat_gateway = true` plus Lambda subnets in both AZs means a NAT-AZ outage silently removes egress for half your Lambda concurrency. The assessor doesn't care (no VPC), but the extractor's Secrets Manager / S3 traffic uses VPC endpoints, not NAT, so you're actually fine there. Still, the asymmetric routing is a future footgun — add a second NAT when anything VPC-bound adds public-internet dependencies.

10. **KMS key policies allow `logs.<region>.amazonaws.com` unconditionally.** No `kms:EncryptionContext` scoping to a specific log group ARN. Any log group in the account that tries to use these CMKs would succeed. Low risk given the bucket policy, but the hardening is cheap.

11. **Log groups encrypted with the S3 CMK.** Mixing purposes in one key complicates rotation, revocation, and blast-radius reasoning. A dedicated `alias/irap-audit-logs` CMK is the conventional shape.

12. **S3 bucket policy doesn't enforce TLS.** Add a `DenyInsecureTransport` statement with `aws:SecureTransport: "false"`. Standard hardening; should be a freebie.

### P3 — Observability

13. **No alerting on "silent success".** Today's design will cheerfully produce an empty report if `general_log` was rotated mid-week, or if the extractor returned zero rows. The SNS email will say "0 FAIL" — humans will trust it.
    *Fix:* CloudWatch alarms on (a) extractor `row_count == 0`, (b) more than 8 days since last object in `reports/`, (c) Bedrock invocation errors, (d) agent-loop iterations > N.

14. **`trace_callback` logs fine-grained events but no metrics.** Great for debugging, not usable for dashboards or alarms. Emit EMF-formatted metrics (rows extracted, findings count, tool invocations, prompt tokens) so CloudWatch can chart them.

15. **No cost guardrail on Bedrock.** A runaway agent loop on a large CSV could produce a surprise bill. Add a Bedrock usage budget and an alarm on assessor Lambda duration > 60s as a canary.

## 5. Trade-offs made, surfaced explicitly

| Decision | Benefit | Cost you accepted |
|---|---|---|
| Assessor outside the VPC | No NAT/Bedrock-VPC-endpoint complexity; cheaper | PROTECTED data traverses public endpoint — requires explicit IRAP boundary confirmation |
| `general_log` as source | Zero infra to stand up; easy demo | Performance hit on RDS; incomplete audit trail under real load; not ISM-1405 best practice |
| Single weekly batch | Simple; cheap; matches "weekly review" rhythm | Up to 7 days of detection latency; one missed run = a full week gap |
| S3 event → Lambda (no queue) | Minimum infrastructure | No DLQ, no retry budget, no idempotency substrate |
| Full CSV into model context | Agent can reason freely | Does not scale past dev-sized workloads |
| Free-text JSON response + regex parse | Works with any Strands model | Breaks on any prompt/model drift |
| Closure-factory tool | Prevents LLM from picking the key | Tool signature is unusual; a new reader has to look twice |
| Versioning, no Object Lock | Recovery from typos | Not tamper-evident — weak posture for a compliance artefact |

## 6. What I'd revisit as the system grows

In the order I'd tackle them:

1. **Move off `general_log`** to MySQL Audit Plugin → Firehose → S3 partitioned by date/hour. Keep the current pipeline shape; only the source changes.
2. **Put a queue between S3 and the assessor** (SQS with DLQ, or Step Functions). Buys idempotency keys, retries, and a place to hang human-review steps.
3. **Rebuild the agent's toolkit** to return aggregates. The agent should *never* see raw rows unless it's drilling into a specific finding's evidence.
4. **Switch to tool-based structured output** (`submit_findings`) — delete the regex parse.
5. **Enable Object Lock** on a new bucket (requires re-creation; do it before you have production reports). Co-locate a `.sha256` sidecar for each report.
6. **Ingest findings into a durable store** (DynamoDB or a small Aurora Serverless table) so you can query trends across weeks — the current CSV-in-S3 pattern is write-only.
7. **Add Athena + Glue catalog** over `raw/` — humans will want ad-hoc queries, and this lets you defend the agent's findings with "here's the exact row set it saw".
8. **Split the CMK per purpose** — s3, secrets, sns, logs each get their own.
9. **Multi-account pattern** — audit-bucket in a dedicated security account, assessor role assumes into it. Aligns with how most IRAP-assessed orgs organise their evidence stores.
10. **Human review gate** before SNS notification once volume grows — a reviewer either accepts findings as-is or annotates before distribution.

## 7. Assumptions I made (flag any that are wrong)

- The system is currently demo / pre-production; "assessor-ready" is a quality target, not a live IRAP submission pipeline.
- The workload is genuinely PROTECTED-classified in your SSP (not just a rehearsal with dummy data).
- You have, or will have, an IRAP assessor in the loop who'll flag ISM controls beyond the four modelled here — the system isn't claiming to cover the full ISM.
- The weekly cadence is a requirement, not an arbitrary starting point.
