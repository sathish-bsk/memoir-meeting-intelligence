# ─── AWS Configuration ─────────────────────────────────────────────
variable "aws_region" {
  description = "AWS region to deploy in"
  type        = string
  default     = "ap-south-1"
}

# ─── EC2 Instance ──────────────────────────────────────────────────
variable "instance_type" {
  description = "EC2 instance type (t3.small = 2 vCPU, 2 GB RAM)"
  type        = string
  default     = "t3.small"
}

variable "volume_size" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 30
}

# ─── SSH Access ────────────────────────────────────────────────────
variable "ssh_public_key_path" {
  description = "Path to SSH public key file (e.g. ~/.ssh/memoir-prod-key.pub)"
  type        = string
  # No default — user MUST provide
}

variable "ssh_allowed_cidrs" {
  description = "CIDR blocks allowed to SSH. Find your IP: curl ifconfig.me"
  type        = list(string)
  # No default — user MUST specify their IP(s)
}

# ─── GPU Access (Vast.ai) ─────────────────────────────────────────
variable "gpu_ip" {
  description = "Vast.ai GPU instance IP. Leave empty if not yet provisioned."
  type        = string
  default     = ""
}

# ─── Tagging ───────────────────────────────────────────────────────
variable "project" {
  description = "Project name for resource tagging"
  type        = string
  default     = "memoir"
}

variable "environment" {
  description = "Environment name (prod, staging, dev)"
  type        = string
  default     = "prod"
}
