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
