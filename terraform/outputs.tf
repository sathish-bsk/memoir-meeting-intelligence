output "elastic_ip" {
  description = "Public Elastic IP — point your DNS A record here"
  value       = aws_eip.memoir_prod.public_ip
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.memoir_prod.id
}

output "security_group_id" {
  description = "Security group ID (needed when setting up RDS security group rules)"
  value       = aws_security_group.memoir_prod.id
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh memoir@${aws_eip.memoir_prod.public_ip}"
}
