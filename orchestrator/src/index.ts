import "dotenv/config";
import express from "express";
import config from "./config";
import router from "./api/router";
import { ensureNetwork } from "./docker/manager";
import { startDockerEventListener } from "./docker/events";
import { createBotWorker } from "./queue/worker";
import { startReaper, stopReaper } from "./services/reaper";
import { startPool, stopPool } from "./pool/pool-manager";
import { logger } from "./logger";

async function main(): Promise<void> {
  logger.info("Starting Bot Orchestrator...");

  // 1. Start Express API (always — handles events, DB inserts, etc.)
  const app = express();
  app.use(express.json({ limit: "1mb" }));
  app.use(router);

  const server = app.listen(config.PORT, () => {
    logger.info({ port: config.PORT }, "Orchestrator API listening");
  });

  // 2. Start BullMQ worker (processes jobs even if Docker isn't ready yet)
  const worker = createBotWorker();

  // 3. Docker-dependent setup (non-fatal — bot dispatch fails but API still works)
  try {
    await ensureNetwork();
    await startDockerEventListener();
    startReaper();
    // Pool startup is gated internally by BOT_POOL_ENABLED; safe to call
    // unconditionally. When enabled, this scans for existing pool members
    // from a prior orchestrator instance and reattaches them, then spawns
    // up to BOT_POOL_SIZE warm bots in the background.
    await startPool();
    logger.info("Docker integration ready");
  } catch (err: any) {
    logger.warn(
      { err: err.message },
      "Docker not available — API and event handling will work, but bot dispatch will fail. Start Docker Desktop to enable."
    );
  }

  logger.info("Bot Orchestrator started");

  // Graceful shutdown
  const shutdown = async (signal: string) => {
    logger.info({ signal }, "Shutting down...");

    // Stop accepting new connections
    server.close();

    // Stop reaper + pool top-up timer
    stopReaper();
    stopPool();

    // Close worker (wait for active jobs with timeout)
    await worker.close();

    logger.info("Shutdown complete");
    process.exit(0);
  };

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
}

main().catch((err) => {
  logger.fatal({ err }, "Failed to start orchestrator");
  process.exit(1);
});
