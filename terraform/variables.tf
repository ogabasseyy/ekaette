variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "app_name" {
  description = "Application name used for resource naming"
  type        = string
  default     = "ekaette"
}

variable "container_image" {
  description = "Full container image URL (e.g. us-central1-docker.pkg.dev/PROJECT/REPO/IMAGE:TAG)"
  type        = string
}

variable "min_instances" {
  description = "Minimum Cloud Run instances (1 avoids cold start during demo)"
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Maximum Cloud Run instances"
  type        = number
  default     = 10
}

variable "cors_allowed_origins" {
  description = "Allowed CORS origins for the media storage bucket"
  type        = list(string)
  default     = ["https://ekaette.vercel.app"]
}

variable "env_vars" {
  description = "Additional environment variables for the Cloud Run service"
  type        = map(string)
  default     = {}
}

variable "cpu" {
  description = "CPU allocation for Cloud Run containers"
  type        = string
  default     = "2"
}

variable "memory" {
  description = "Memory allocation for Cloud Run containers"
  type        = string
  default     = "1Gi"
}

variable "timeout" {
  description = "Request timeout for Cloud Run service (e.g. 3600s for long-running audio sessions)"
  type        = string
  default     = "3600s"
}

variable "deletion_protection" {
  description = "Enable deletion protection for the Cloud Run service"
  type        = bool
  default     = false
}

variable "ingress" {
  description = "Ingress traffic setting for Cloud Run (INGRESS_TRAFFIC_ALL, INGRESS_TRAFFIC_INTERNAL_ONLY, etc.)"
  type        = string
  default     = "INGRESS_TRAFFIC_ALL"
}

variable "kms_key_name" {
  description = "Cloud KMS key for CMEK encryption of the media bucket (null = Google-managed keys)"
  type        = string
  default     = null
}

# ── WhatsApp Cloud Tasks ──

variable "wa_cloud_tasks_max_attempts" {
  description = "Max retry attempts for WhatsApp webhook processing tasks"
  type        = number
  default     = 3
}

variable "wa_cloud_tasks_queue_name" {
  description = "Cloud Tasks queue name for WhatsApp webhook processing"
  type        = string
  default     = "wa-webhook-processing"
}

# ── WhatsApp Replay Artifacts ──

variable "wa_replay_bucket_name" {
  description = "Dedicated GCS bucket for WhatsApp replay artifacts"
  type        = string
  default     = ""
}

variable "wa_replay_blob_prefix" {
  description = "Blob prefix for replay artifacts in the replay bucket"
  type        = string
  default     = "wa/replay/"
}

variable "wa_replay_blob_ttl_days" {
  description = "Retention days for WhatsApp replay artifacts"
  type        = number
  default     = 1
}

# ── WhatsApp Edge / LB ──

variable "wa_webhook_domain" {
  description = "Public domain for Meta webhook (e.g. wa-webhook.ekaette.com)"
  type        = string
  default     = ""
}

variable "wa_service_api_domain" {
  description = "Service API domain for VM/Cloud Tasks traffic (e.g. wa-service.ekaette.com)"
  type        = string
  default     = ""
}

variable "app_public_domain" {
  description = "Existing public app domain (e.g. app.ekaette.com)"
  type        = string
  default     = ""
}

variable "wa_vm_egress_cidrs" {
  description = "Trusted VM/NAT CIDRs allowed to call /whatsapp/send"
  type        = list(string)
  default     = []
}
