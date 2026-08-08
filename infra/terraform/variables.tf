variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "eu-west-1"
}

variable "environment" {
  description = "Environment name, used as a suffix on every resource."
  type        = string
  default     = "prod"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,16}$", var.environment))
    error_message = "environment must be 2-16 lowercase alphanumeric or hyphen characters."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "availability_zone_count" {
  description = "Number of AZs to spread subnets across. RDS requires at least two."
  type        = number
  default     = 2

  validation {
    condition     = var.availability_zone_count >= 2
    error_message = "At least two availability zones are required for the RDS subnet group."
  }
}

variable "api_image" {
  description = "Fully qualified image reference for the API task. Immutable tag, never :latest."
  type        = string

  validation {
    condition     = !endswith(var.api_image, ":latest")
    error_message = "Deploy an immutable tag so a rollback can name the exact prior image."
  }
}

variable "api_cpu" {
  description = "Fargate CPU units for the API task."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate memory (MiB) for the API task."
  type        = number
  default     = 1024
}

variable "api_desired_count" {
  description = "Number of API tasks to run."
  type        = number
  default     = 2
}

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.micro is enough for the indexed corpus."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GiB."
  type        = number
  default     = 20
}

variable "db_deletion_protection" {
  description = "Block accidental deletion of the database holding the index."
  type        = bool
  default     = true
}

variable "anthropic_api_key" {
  description = <<-EOT
    Provider API key. Written to Secrets Manager and injected into the task at
    runtime, never baked into the image. Supply via TF_VAR_anthropic_api_key
    rather than a committed tfvars file.
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 30
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs permitted to reach the load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
