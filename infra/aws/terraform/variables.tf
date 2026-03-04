variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name prefix for AWS resources"
  type        = string
  default     = "ekaette-nova"
}

variable "container_image" {
  description = "ECS image URI"
  type        = string
}

variable "container_port" {
  description = "Container port exposed by API"
  type        = number
  default     = 8080
}

variable "desired_count" {
  description = "ECS desired task count"
  type        = number
  default     = 1
}

variable "cpu" {
  description = "Fargate task CPU units"
  type        = number
  default     = 1024
}

variable "memory" {
  description = "Fargate task memory (MiB)"
  type        = number
  default     = 2048
}

variable "alb_idle_timeout_seconds" {
  description = "ALB idle timeout for long-lived websocket connections"
  type        = number
  default     = 120
}

variable "app_env_vars" {
  description = "Plaintext env vars for task definition"
  type        = map(string)
  default = {
    APP_ENV       = "production"
    LOG_LEVEL     = "INFO"
    LLM_PROVIDER  = "amazon_nova"
    AWS_REGION    = "us-east-1"
    NOVA_FAIL_FAST = "true"
  }
}

variable "secret_arns" {
  description = "Map of ENV_NAME => Secrets Manager ARN"
  type        = map(string)
  default     = {}
}

