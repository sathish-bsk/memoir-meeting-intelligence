terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ─── Data: Find latest Ubuntu 22.04 AMI ───────────────────────────
# Auto-discovers the AMI — no hardcoded ID that breaks across regions/time

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical's AWS account ID

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ─── SSH Key Pair ──────────────────────────────────────────────────
# Reads user's public key file and uploads to AWS.
# Private key never touches Terraform state.

resource "aws_key_pair" "memoir_prod" {
  key_name   = "${var.project}-${var.environment}-key"
  public_key = file(var.ssh_public_key_path)

  tags = {
    Name        = "${var.project}-${var.environment}-key"
    Project     = var.project
    Environment = var.environment
  }
}

# ─── Security Group ───────────────────────────────────────────────
# Firewall rules: SSH (restricted), HTTP, HTTPS open

resource "aws_security_group" "memoir_prod" {
  name        = "${var.project}-${var.environment}-sg"
  description = "Memoir production — SSH (restricted), HTTP, HTTPS"

  # SSH — only from specified IPs
  ingress {
    description = "SSH from allowed IPs"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_allowed_cidrs
  }

  # HTTP — Traefik redirects to HTTPS
  ingress {
    description = "HTTP (Traefik redirect to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTPS — all public traffic via Traefik
  ingress {
    description = "HTTPS (Traefik)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # All outbound traffic
  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project}-${var.environment}-sg"
    Project     = var.project
    Environment = var.environment
  }
}

# GPU PostgreSQL rule — only created when gpu_ip is set
# Uses count conditional: 0 rules when empty, 1 rule when IP provided
resource "aws_security_group_rule" "gpu_postgres" {
  count             = var.gpu_ip != "" ? 1 : 0
  type              = "ingress"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  cidr_blocks       = ["${var.gpu_ip}/32"]
  security_group_id = aws_security_group.memoir_prod.id
  description       = "PostgreSQL from Vast.ai GPU"
}

# ─── EC2 Instance ─────────────────────────────────────────────────
# Ubuntu 22.04, runs setup-production.sh on first boot via user_data

resource "aws_instance" "memoir_prod" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.memoir_prod.key_name
  vpc_security_group_ids = [aws_security_group.memoir_prod.id]

  root_block_device {
    volume_size = var.volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  # Run setup script on first boot
  # The script installs Python, Node, Docker, PostgreSQL, Redis,
  # creates systemd units, and configures the server
  user_data = file("${path.module}/../scripts/setup-production.sh")

  # IMPORTANT: Changing user_data would force instance REPLACEMENT
  # (destroy + recreate). Since user_data only runs on first boot,
  # we ignore changes to it — update the server via SSH instead.
  lifecycle {
    ignore_changes = [user_data]
  }

  tags = {
    Name        = "${var.project}-${var.environment}"
    Project     = var.project
    Environment = var.environment
  }
}

# ─── Elastic IP ────────────────────────────────────────────────────
# Static IP that survives instance stop/start.
# Free while attached to a running instance.

resource "aws_eip" "memoir_prod" {
  instance = aws_instance.memoir_prod.id
  domain   = "vpc"

  tags = {
    Name        = "${var.project}-${var.environment}-eip"
    Project     = var.project
    Environment = var.environment
  }
}
