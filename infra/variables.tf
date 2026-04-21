variable "rds_username" {
  description = "Master username for the RDS MySQL instance"
  type        = string
  default     = "irap_admin"
}

variable "alert_email" {
  description = "Email address for SNS compliance notifications"
  type        = string
}

variable "ci_role_name" {
  description = "IAM role name used by CI/CD to deploy (exempted from S3 bucket deny policy)"
  type        = string
  default     = "github-actions-irap-audit-agent"
}
