# BotWorker Deployment Guide

## Prerequisites

- AWS account with ECR and EC2/ECS access
- Docker installed locally
- TranscriptionBase service deployed

## Building the Docker Image

```bash
docker build -t botworker:latest .

# Tag for ECR
docker tag botworker:latest <account>.dkr.ecr.<region>.amazonaws.com/botworker:latest

# Push
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker push <account>.dkr.ecr.<region>.amazonaws.com/botworker:latest
```

## EC2 Single Instance Deployment

### Instance Setup

1. Launch a **t3.xlarge** (4 vCPU, 16GB RAM) — Chromium needs the memory
2. Use Amazon Linux 2023 or Ubuntu 22.04
3. Install Docker:
   ```bash
   sudo yum install -y docker  # Amazon Linux
   sudo systemctl enable --now docker
   ```
4. Configure security group: allow port 8000 from your backend's security group

### Run

```bash
docker run -d \
  --name botworker \
  --restart unless-stopped \
  --shm-size=2g \
  -p 8000:8000 \
  -e STORAGE_BACKEND=local \
  -e BACKEND_URL=https://your-backend.com \
  -e TRANSCRIPTION_URL=https://your-transcription.com \
  -e LOG_LEVEL=WARNING \
  -v /data/recordings:/tmp/recordings \
  <account>.dkr.ecr.<region>.amazonaws.com/botworker:latest
```

### Systemd Service (optional)

Create `/etc/systemd/system/botworker.service`:
```ini
[Unit]
Description=BotWorker Meeting Bot
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker stop botworker
ExecStartPre=-/usr/bin/docker rm botworker
ExecStart=/usr/bin/docker run --name botworker --shm-size=2g -p 8000:8000 -e BACKEND_URL=https://your-backend.com <image>
ExecStop=/usr/bin/docker stop botworker

[Install]
WantedBy=multi-user.target
```

## ECS on EC2 Deployment (for scaling)

### Task Definition

Key settings:
- **Memory**: 8192 MB
- **CPU**: 4096 (4 vCPU)
- **LinuxParameters.SharedMemorySize**: 2048 (2GB for Chromium)
- **Essential**: true
- **Log driver**: awslogs

### ECS Service

- Desired count: based on expected concurrent meetings
- **One bot per task** — each container handles one meeting at a time
- Launch type: EC2 (not Fargate — need `shm_size` control)

### Auto Scaling

- Scale EC2 instances based on ECS capacity providers
- Scale ECS service based on queue depth or CPU utilization
- Each t3.xlarge can run ~2 bot containers

### Load Balancer

- Application Load Balancer for routing `/join` requests to available bots
- Health check path: `/health`
- Health check interval: 30s

## Environment Variables for Production

```bash
STORAGE_BACKEND=local          # or s3 when implemented
BACKEND_URL=https://your-backend.com
TRANSCRIPTION_URL=https://your-transcription-api.com
LOG_LEVEL=WARNING
MEETING_MAX_DURATION_SECONDS=14400
HEARTBEAT_INTERVAL_SECONDS=30
```

## Monitoring

- **Health check**: ALB/ECS health checks on `/health`
- **Logs**: CloudWatch via Docker awslogs driver
- **Heartbeat events**: Backend receives heartbeats every 30s as liveness signal
- **Status endpoint**: `/status` provides real-time state

## Security

- No AWS credentials stored in the bot container (presigned URLs for S3 mode)
- Place bot containers in a **private subnet**
- Security group: only the backend can reach port 8000
- No inbound internet access needed (bot connects outbound to Google Meet)

## Cost Estimation

| Instance | vCPU | RAM | Bots/Instance | On-Demand $/hr | Monthly (24/7) |
|----------|------|-----|---------------|----------------|----------------|
| t3.large | 2 | 8GB | 1 | $0.083 | ~$60 |
| t3.xlarge | 4 | 16GB | 2 | $0.166 | ~$120 |
| c5.xlarge | 4 | 8GB | 2 | $0.170 | ~$122 |

Spot instances can reduce costs by 60-70% for non-critical workloads.
