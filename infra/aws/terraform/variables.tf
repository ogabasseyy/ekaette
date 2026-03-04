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

variable "alb_certificate_arn" {
  description = "ACM certificate ARN for the public HTTPS ALB listener"
  type        = string
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

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096, 8192, 16384], var.cpu)
    error_message = "Fargate CPU must be one of: 256, 512, 1024, 2048, 4096, 8192, 16384."
  }
}

variable "memory" {
  description = "Fargate task memory (MiB)"
  type        = number
  default     = 2048

  validation {
    condition = (
      (var.cpu == 256 && contains([512, 1024, 2048], var.memory)) ||
      (var.cpu == 512 && contains([1024, 2048, 3072, 4096], var.memory)) ||
      (var.cpu == 1024 && var.memory >= 2048 && var.memory <= 8192 && var.memory % 1024 == 0) ||
      (var.cpu == 2048 && var.memory >= 4096 && var.memory <= 16384 && var.memory % 1024 == 0) ||
      (var.cpu == 4096 && var.memory >= 8192 && var.memory <= 30720 && var.memory % 1024 == 0) ||
      (var.cpu == 8192 && var.memory >= 16384 && var.memory <= 61440 && var.memory % 4096 == 0) ||
      (var.cpu == 16384 && var.memory >= 32768 && var.memory <= 122880 && var.memory % 8192 == 0)
    )
    error_message = "Invalid Fargate CPU/memory pair for ECS. Update cpu/memory to a supported AWS Fargate combination."
  }
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
    APP_ENV        = "production"
    LOG_LEVEL      = "INFO"
    LLM_PROVIDER   = "amazon_nova"
    NOVA_FAIL_FAST = "true"
  }
}

variable "secret_arns" {
  description = "Map of ENV_NAME => Secrets Manager ARN"
  type        = map(string)
  default     = {}
}
