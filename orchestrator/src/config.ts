const config = {
  PORT: parseInt(process.env.PORT || "4000", 10),
  REDIS_URL: process.env.REDIS_URL || "redis://localhost:6379",
  QUEUE_NAME: process.env.QUEUE_NAME || "bot-jobs",
  MAX_CONCURRENT_BOTS: parseInt(process.env.MAX_CONCURRENT_BOTS || "10", 10),

  // Bot container settings
  BOT_IMAGE: process.env.BOT_IMAGE || "botworker:latest",
  BOT_MEMORY_LIMIT: parseInt(process.env.BOT_MEMORY_LIMIT || String(2 * 1024 * 1024 * 1024), 10),
  BOT_CPU_LIMIT: parseInt(process.env.BOT_CPU_LIMIT || String(2e9), 10),
  BOT_SHM_SIZE: parseInt(process.env.BOT_SHM_SIZE || String(2 * 1024 * 1024 * 1024), 10),
  BOT_NETWORK: process.env.BOT_NETWORK || "bot-network",

  // Lifecycle
  HEARTBEAT_TIMEOUT_MS: parseInt(process.env.HEARTBEAT_TIMEOUT_MS || "300000", 10),
  ZOMBIE_REAPER_INTERVAL_MS: parseInt(process.env.ZOMBIE_REAPER_INTERVAL_MS || "60000", 10),
  MAX_MEETING_DURATION_MS: parseInt(process.env.MAX_MEETING_DURATION_MS || "14400000", 10),
  HEALTH_CHECK_TIMEOUT_MS: parseInt(process.env.HEALTH_CHECK_TIMEOUT_MS || "60000", 10),
  // Tier 1.7: tightened from 2000 ms to 250 ms. The old 2 s cadence meant
  // we sat idle up to 2 s after the bot's /health turned green before
  // sending /join. Bot uvicorn easily handles 4 req/s. Current poll loop
  // (worker.ts:250) logs only on success, not per attempt — so no log spam.
  HEALTH_CHECK_INTERVAL_MS: parseInt(process.env.HEALTH_CHECK_INTERVAL_MS || "250", 10),
  CONTAINER_STOP_TIMEOUT_S: parseInt(process.env.CONTAINER_STOP_TIMEOUT_S || "30", 10),

  // Pre-warmed bot pool (Tier 2.1 — plan/bot_prewarmed_pool.md).
  // OFF by default; flip BOT_POOL_ENABLED=true after Phase 1+2 are deployed.
  // BOT_POOL_SIZE=1 for v1 on t3.micro; raise to 2-3 only after instance
  // upgrade. BOT_POOL_MAX_IDLE_AGE_MS=15min tightened from the v1.4 plan's
  // 30min default because Chromium memory creep matters more on a small box.
  BOT_POOL_ENABLED: process.env.BOT_POOL_ENABLED === "true",
  BOT_POOL_SIZE: parseInt(process.env.BOT_POOL_SIZE || "1", 10),
  BOT_POOL_READY_TIMEOUT_MS: parseInt(process.env.BOT_POOL_READY_TIMEOUT_MS || "30000", 10),
  BOT_POOL_CLAIM_TIMEOUT_MS: parseInt(process.env.BOT_POOL_CLAIM_TIMEOUT_MS || "2000", 10),
  BOT_POOL_MAX_IDLE_AGE_MS: parseInt(process.env.BOT_POOL_MAX_IDLE_AGE_MS || "900000", 10),

  // Recordings
  BOT_RECORDINGS_DIR: process.env.BOT_RECORDINGS_DIR || "/data/recordings",

  // URLs
  ORCHESTRATOR_URL: process.env.ORCHESTRATOR_URL || "http://orchestrator:4000",
  BACKEND_CALLBACK_URL: process.env.BACKEND_CALLBACK_URL || "",
  TRANSCRIPTION_WEBHOOK_URL: process.env.TRANSCRIPTION_WEBHOOK_URL || process.env.BACKEND_CALLBACK_URL || "",
  TRANSCRIPTION_URL: process.env.TRANSCRIPTION_URL || "http://host.docker.internal:8000",
  STORAGE_BACKEND: process.env.STORAGE_BACKEND || "local",
  S3_BUCKET: process.env.S3_BUCKET || "",
  S3_ENDPOINT_URL: process.env.S3_ENDPOINT_URL || "",
  S3_REGION: process.env.S3_REGION || "auto",
  AWS_ACCESS_KEY_ID: process.env.AWS_ACCESS_KEY_ID || "",
  AWS_SECRET_ACCESS_KEY: process.env.AWS_SECRET_ACCESS_KEY || "",
  AWS_REGION: process.env.AWS_REGION || process.env.S3_REGION || "ap-south-1",

  // Phase 3 — bot authenticated session. Forwarded into bot containers
  // so they can fetch the Chromium profile tarball from Secrets Manager
  // at join time. All optional — when unset, BotWorker stays in guest mode.
  MEMOIR_BOT_AUTH_MODE: process.env.MEMOIR_BOT_AUTH_MODE || "guest",
  MEMOIR_BOT_INVITE_EMAIL: process.env.MEMOIR_BOT_INVITE_EMAIL || "",
  MEMOIR_BOT_GOOGLE_PROFILE_KEY: process.env.MEMOIR_BOT_GOOGLE_PROFILE_KEY || "",
  MEMOIR_BOT_MICROSOFT_PROFILE_KEY: process.env.MEMOIR_BOT_MICROSOFT_PROFILE_KEY || "",

  // Auth (for forwarding events to backend webhook)
  INTERNAL_SERVICE_KEY: process.env.INTERNAL_SERVICE_KEY || "",

  // Database (for transcription job queue)
  DATABASE_URL: process.env.DATABASE_URL || "postgresql://videoclip:videoclipdev@localhost:5432/videoclip_db",

  LOG_LEVEL: process.env.LOG_LEVEL || "info",
};

export default config;
