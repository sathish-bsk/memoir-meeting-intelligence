import { Worker, Job } from "bullmq";
import config from "../config";
import { BotJobData, BotJobStatus } from "../types";
import {
  ensureImage,
  createBotContainer,
  startContainer,
  getContainerIp,
  getContainerHostPort,
  getContainerLogs,
  waitForContainer,
  stopContainer,
  removeContainer,
  findRunningContainerForJob,
  findExitedContainerForJob,
} from "../docker/manager";
import { updateBot, getBot, deregisterClaim } from "../services/bot-tracker";
import { forwardEvent } from "../services/event-forwarder";
import { logger } from "../logger";
import { claimWarmBot } from "../pool/pool-manager";

export function createBotWorker(): Worker<BotJobData> {
  const worker = new Worker<BotJobData>(
    config.QUEUE_NAME,
    async (job: Job<BotJobData>) => {
      await processJob(job);
    },
    {
      connection: { url: config.REDIS_URL },
      concurrency: config.MAX_CONCURRENT_BOTS,
    }
  );

  worker.on("completed", (job) => {
    logger.info({ jobId: job.data.jobId }, "BullMQ job completed");
  });

  worker.on("failed", (job, err) => {
    logger.error(
      { jobId: job?.data.jobId, err: err.message },
      "BullMQ job failed"
    );
  });

  worker.on("error", (err) => {
    logger.error({ err }, "BullMQ worker error");
  });

  logger.info(
    { concurrency: config.MAX_CONCURRENT_BOTS },
    "BullMQ worker started"
  );
  return worker;
}

async function processJob(job: Job<BotJobData>): Promise<void> {
  const { jobId, meetingUrl, botName } = job.data;
  const log = logger.child({ jobId });

  let containerId: string | null = null;

  try {
    // --- Check 1: Already completed? Skip retry entirely ---
    const bot = await getBot(jobId);
    if (
      bot &&
      [BotJobStatus.COMPLETED, BotJobStatus.FAILED, BotJobStatus.KILLED].includes(bot.status)
    ) {
      log.info(
        { status: bot.status },
        "Job already in terminal state, skipping retry"
      );
      return;
    }

    // --- Check 2: Container still running? Reconnect ---
    const existing = await findRunningContainerForJob(jobId);
    if (existing) {
      log.info(
        { containerId: existing.containerId, ip: existing.ip },
        "Found existing running container, reconnecting"
      );
      containerId = existing.containerId;
      await updateBot(jobId, {
        containerId: existing.containerId,
        containerIp: existing.ip,
      });

      // Resume waiting for container to exit
      const { StatusCode } = await waitForContainer(existing.containerId);
      log.info({ exitCode: StatusCode }, "Reconnected container exited");

      await handleContainerExit(jobId, existing.containerId, StatusCode, log);
      return;
    }

    // --- Check 3: Container exited while orchestrator was down? Handle its exit ---
    const exited = await findExitedContainerForJob(jobId);
    if (exited) {
      log.info(
        { containerId: exited.containerId, exitCode: exited.exitCode },
        "Found exited container from previous run, handling exit"
      );
      await handleContainerExit(jobId, exited.containerId, exited.exitCode, log);
      return;
    }

    // --- No container found — try the pool, else cold-start ---
    await updateBot(jobId, { status: BotJobStatus.CREATING });

    let hostPort: number;
    let fromPool = false;

    const claimed = await claimWarmBot(jobId);
    if (claimed) {
      // Pool claim — pool member's container is already running with
      // Chromium pre-launched at about:blank. Skip ensureImage / create /
      // startContainer / waitForHealth — the pool manager already polled
      // /ready as the stronger gate before flipping to "warm".
      containerId = claimed.containerId;
      hostPort = claimed.hostPort;
      fromPool = true;
      log.info(
        { containerId, hostPort },
        "Using pre-warmed bot from pool"
      );
      await updateBot(jobId, {
        status: BotJobStatus.STARTING,
        containerId,
        startedAt: new Date().toISOString(),
        containerIp: `127.0.0.1:${hostPort}`,
      });
    } else {
      // Cold path — unchanged from pre-pool behavior.
      log.info("Pool empty or disabled — cold-starting bot");
      await ensureImage();
      const container = await createBotContainer(job.data);
      containerId = container.id;
      await startContainer(container);
      await updateBot(jobId, {
        status: BotJobStatus.STARTING,
        containerId: container.id,
        startedAt: new Date().toISOString(),
      });
      const port = await getContainerHostPort(container.id);
      if (!port) {
        throw new Error("Could not determine container host port");
      }
      hostPort = port;
      await updateBot(jobId, { containerIp: `127.0.0.1:${hostPort}` });
      log.info({ hostPort, containerId: container.id }, "Container started");
      // /health gate — pool members already pass /ready (stronger gate) so
      // we skip /health for that path.
      const botUrl = `http://127.0.0.1:${hostPort}`;
      await waitForHealth(botUrl);
    }

    const botUrl = `http://127.0.0.1:${hostPort}`;

    // Send /join to the bot — forward "joining" status to core_api
    await updateBot(jobId, { status: BotJobStatus.JOINING });
    await forwardEvent({
      jobId,
      eventType: "bot_joining",
      timestamp: new Date().toISOString(),
    }).catch((err) => {
      log.warn({ err }, "Failed to forward bot_joining event");
    });
    // Tier 0: capture dispatch_sent_at so the bot's phase_log can compute
    // t_since_dispatch_ms for every phase boundary. Sent as unix seconds
    // (float) for parity with Python's time.time().
    const dispatchSentAt = Date.now() / 1000;
    log.info(
      { dispatch_sent_at: dispatchSentAt, fromPool },
      "Sending /join to bot..."
    );

    const joinResp = await fetch(`${botUrl}/join`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: jobId,
        meeting_url: meetingUrl,
        bot_name: botName || "Meeting Notetaker",
        dispatch_sent_at: dispatchSentAt,
        // Pool members boot with AUTO_EXIT=0 so they idle until claimed.
        // Tell the bot to flip AUTO_EXIT on for this meeting so it exits
        // after the meeting, matching today's one-shot lifecycle. Cold
        // bots already have AUTO_EXIT=1 from the orchestrator's container
        // env — this flag is a no-op for them.
        auto_exit_after: fromPool,
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (!joinResp.ok) {
      const body = await joinResp.text();
      throw new Error(`Bot /join failed (${joinResp.status}): ${body}`);
    }

    log.info("Bot /join accepted, waiting for meeting to complete...");

    // Wait for container to exit. After the pool/cold consolidation,
    // `container.id` is no longer in scope on the pool branch — use
    // containerId (set in both branches).
    const { StatusCode } = await waitForContainer(containerId);
    log.info({ exitCode: StatusCode }, "Bot container exited");

    await handleContainerExit(jobId, containerId, StatusCode, log);
  } catch (err: any) {
    log.error({ err: err.message }, "Job processing failed");

    await updateBot(jobId, {
      status: BotJobStatus.FAILED,
      endedAt: new Date().toISOString(),
      error: err.message,
    });

    // Notify core_api so meeting status gets updated
    await forwardEvent({
      jobId,
      eventType: "bot_error",
      timestamp: new Date().toISOString(),
      data: { error: err.message },
    }).catch((fwdErr) => {
      log.error({ err: fwdErr }, "Failed to forward bot_error event");
    });

    // Cleanup container on failure
    if (containerId) {
      try {
        await stopContainer(containerId, 10);
      } catch {
        // ignore
      }
      try {
        await removeContainer(containerId);
      } catch {
        // ignore
      }
      // Also drop any pool claim entry to keep Redis clean.
      try {
        await deregisterClaim(containerId);
      } catch {
        // ignore
      }
    }

    throw err; // Re-throw so BullMQ retries if attempts remain
  }
}

async function handleContainerExit(
  jobId: string,
  containerId: string,
  exitCode: number,
  log: typeof logger
): Promise<void> {
  // Collect logs
  try {
    const logs = await getContainerLogs(containerId);
    log.debug({ logsLength: logs.length }, "Collected container logs");
  } catch {
    log.warn("Could not collect container logs");
  }

  // Update final status based on exit code (if not already terminal)
  const bot = await getBot(jobId);
  const alreadyTerminal =
    bot &&
    [BotJobStatus.COMPLETED, BotJobStatus.FAILED, BotJobStatus.KILLED].includes(
      bot.status
    );

  if (!alreadyTerminal) {
    if (exitCode === 0) {
      await updateBot(jobId, {
        status: BotJobStatus.COMPLETED,
        endedAt: new Date().toISOString(),
      });
    } else {
      await updateBot(jobId, {
        status: BotJobStatus.FAILED,
        endedAt: new Date().toISOString(),
        error: `Container exited with code ${exitCode}`,
      });
    }
  }

  // Remove container
  await removeContainer(containerId);
  // Drop the pool:claim:<containerId> Redis index entry if this was a pool
  // claim. No-op for cold bots (key absent). Prevents stale entries from
  // confusing reaper / orchestrator-restart recovery.
  try {
    await deregisterClaim(containerId);
  } catch (err: any) {
    log.warn({ err: err.message, containerId }, "deregisterClaim failed");
  }
}

async function waitForHealth(botUrl: string): Promise<void> {
  const deadline = Date.now() + config.HEALTH_CHECK_TIMEOUT_MS;

  while (Date.now() < deadline) {
    try {
      const resp = await fetch(`${botUrl}/health`, {
        signal: AbortSignal.timeout(5000),
      });
      if (resp.ok) {
        logger.info({ botUrl }, "Bot container is healthy");
        return;
      }
    } catch {
      // Container not ready yet
    }
    await sleep(config.HEALTH_CHECK_INTERVAL_MS);
  }

  throw new Error(
    `Container health check timed out after ${config.HEALTH_CHECK_TIMEOUT_MS}ms`
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
