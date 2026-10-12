/**
 * Pre-warmed BotWorker pool (Tier 2.1 — plan/bot_prewarmed_pool.md).
 *
 * Maintains N=BOT_POOL_SIZE warm BotWorker containers. Each warm container
 * has Xvfb + PulseAudio + Chromium at about:blank pre-launched, so when a
 * meeting job arrives the orchestrator just POSTs /join and the bot starts
 * navigating to the meeting URL within ~3-5 s instead of ~15-20 s cold.
 *
 * Design constraints (see plan §2 + §1.5.x changelogs):
 *
 *   - Single-use pool members. Each warm container handles exactly one
 *     meeting then exits (matches today's one-shot cold-bot lifecycle).
 *   - Lazy refill. Don't spawn a replacement while a meeting is active —
 *     on t3.micro that would mean two Chromiums on a 914 MB box = OOM.
 *   - Empty bot.job_id label at boot. Claims are tracked in Redis via
 *     bot-tracker.registerClaim — labels are immutable on running
 *     containers.
 *   - 15-min idle recycle. Chromium accumulates memory over time; on a
 *     small box this matters.
 *   - Lookup parity: reaper + worker reconnect paths consult Redis via
 *     bot-tracker.lookupJobIdByContainer when bot.job_id label is empty
 *     and bot.pool_member=true.
 */
import {
  createPoolBotContainer,
  ensureImage,
  startContainer,
  removeContainer,
  getContainerHostPort,
  listBotContainers,
} from "../docker/manager";
import {
  registerClaim,
  lookupJobIdByContainer,
  listBots,
} from "../services/bot-tracker";
import { BotJobStatus } from "../types";
import { logger } from "../logger";
import config from "../config";

type PoolState = "warming" | "warm" | "claimed" | "broken";

interface PoolEntry {
  containerId: string;
  hostPort: number;
  state: PoolState;
  createdAt: number;
  lastReadyAt?: number;
}

const pool: PoolEntry[] = [];
let topUpInFlight = 0;
let reapTimer: ReturnType<typeof setInterval> | null = null;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Count meetings currently in flight. Lazy-refill gates on this — we never
 * spawn a warming replacement while an active bot is running, to avoid
 * two-Chromium memory contention on t3.micro.
 *
 * Includes CREATING because worker.processJob sets that status BEFORE
 * calling claimWarmBot (worker.ts: "await updateBot(jobId, { status:
 * CREATING })" → "claimWarmBot(...)" → registerClaim → topUpPool). Without
 * CREATING in this set, topUpPool fired from inside claimWarmBot would see
 * 0 active meetings even though a Chromium is about to start, defeating
 * the entire single-Chromium guarantee on t3.micro.
 *
 * QUEUED is deliberately NOT included — at QUEUED there's no container
 * yet (BullMQ has only queued the job), so a warming replacement doesn't
 * compete with anything yet.
 */
async function countActiveMeetings(): Promise<number> {
  let total = 0;
  for (const status of [
    BotJobStatus.CREATING,
    BotJobStatus.STARTING,
    BotJobStatus.JOINING,
    BotJobStatus.RECORDING,
    BotJobStatus.PROCESSING,
  ]) {
    const bots = await listBots(status);
    total += bots.length;
  }
  return total;
}

async function pollForReady(entry: PoolEntry): Promise<void> {
  const deadline = Date.now() + config.BOT_POOL_READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(`http://127.0.0.1:${entry.hostPort}/ready`, {
        signal: AbortSignal.timeout(2000),
      });
      if (resp.ok) {
        entry.state = "warm";
        entry.lastReadyAt = Date.now();
        logger.info(
          {
            containerId: entry.containerId,
            warmMs: Date.now() - entry.createdAt,
          },
          "pool member warm"
        );
        return;
      }
    } catch {
      /* not ready yet */
    }
    await sleep(250);
  }
  throw new Error("pool member did not become ready within timeout");
}

async function spawnPoolMember(): Promise<void> {
  const container = await createPoolBotContainer();
  await startContainer(container);
  const hostPort = await getContainerHostPort(container.id);
  if (!hostPort) {
    try {
      await removeContainer(container.id);
    } catch {
      /* best-effort */
    }
    throw new Error("no host port assigned to pool container");
  }
  const entry: PoolEntry = {
    containerId: container.id,
    hostPort,
    state: "warming",
    createdAt: Date.now(),
  };
  pool.push(entry);
  // Fire and forget — pollForReady flips state to "warm" or "broken".
  pollForReady(entry).catch((err) => {
    logger.warn(
      { err: err.message, containerId: entry.containerId },
      "pool member failed to warm"
    );
    entry.state = "broken";
  });
}

async function topUpPool(): Promise<void> {
  if (!config.BOT_POOL_ENABLED) return;

  // Lazy refill: don't spawn a warming replacement while a meeting is
  // active. On t3.micro, an active Chromium + a warming Chromium would
  // OOM-kill the host. Cost: while a meeting is running, the pool is
  // empty and subsequent meetings cold-start until the active one ends.
  const activeMeetings = await countActiveMeetings();
  if (activeMeetings > 0) {
    logger.debug({ activeMeetings }, "pool: deferring refill while meeting active");
    return;
  }

  const target = config.BOT_POOL_SIZE;
  const aliveCount = pool.filter((e) => e.state !== "broken").length;
  const needed = Math.max(0, target - aliveCount - topUpInFlight);
  for (let i = 0; i < needed; i++) {
    topUpInFlight++;
    spawnPoolMember()
      .catch((err) => logger.warn({ err: err.message }, "pool top-up failed"))
      .finally(() => {
        topUpInFlight--;
      });
  }
}

async function reapAndTopUp(): Promise<void> {
  for (const e of [...pool]) {
    if (e.state === "broken") {
      logger.info({ containerId: e.containerId }, "reaping broken pool member");
      try {
        await removeContainer(e.containerId);
      } catch {
        /* best-effort */
      }
      const idx = pool.indexOf(e);
      if (idx !== -1) pool.splice(idx, 1);
    } else if (
      e.state === "warm" &&
      e.lastReadyAt !== undefined &&
      Date.now() - e.lastReadyAt > config.BOT_POOL_MAX_IDLE_AGE_MS
    ) {
      logger.info(
        {
          containerId: e.containerId,
          ageMs: Date.now() - e.createdAt,
        },
        "recycling stale warm pool member"
      );
      try {
        await removeContainer(e.containerId);
      } catch {
        /* best-effort */
      }
      const idx = pool.indexOf(e);
      if (idx !== -1) pool.splice(idx, 1);
    }
  }
  await topUpPool();
}

/**
 * Claim a warm pool member for a real meeting job. Returns null if no warm
 * bot is available within BOT_POOL_CLAIM_TIMEOUT_MS, in which case the
 * caller (worker.ts) falls back to the cold-start path.
 */
export async function claimWarmBot(
  jobId: string
): Promise<{ containerId: string; hostPort: number } | null> {
  if (!config.BOT_POOL_ENABLED) return null;

  const deadline = Date.now() + config.BOT_POOL_CLAIM_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const idx = pool.findIndex((e) => e.state === "warm");
    if (idx !== -1) {
      const entry = pool[idx];
      entry.state = "claimed";
      pool.splice(idx, 1);
      logger.info(
        { jobId, containerId: entry.containerId, hostPort: entry.hostPort },
        "pool: claim hit"
      );
      // Record containerId -> jobId in Redis so the reaper + worker
      // reconnect paths can resolve when the Docker label is empty.
      await registerClaim(entry.containerId, jobId);
      // Trigger lazy refill in the background. Will no-op if a meeting
      // is already active (this very claim is one of them once it dispatches).
      void topUpPool();
      return { containerId: entry.containerId, hostPort: entry.hostPort };
    }
    await sleep(50);
  }
  logger.info({ jobId }, "pool: claim miss — falling back to cold start");
  void topUpPool();
  return null;
}

/**
 * Initialize the pool on orchestrator startup.
 *
 * Reattaches to any pool-member containers left running by a prior
 * orchestrator instance:
 *   - Unclaimed (no Redis claim entry, empty bot.job_id label) → poll /ready
 *     and re-add to the pool.
 *   - Claimed (Redis claim entry present) → leave alone; the worker's
 *     existing reconnect path (Check 2 in processJob) will handle them.
 *   - Stopped / non-running → remove.
 */
export async function startPool(): Promise<void> {
  if (!config.BOT_POOL_ENABLED) {
    logger.info("BOT_POOL_ENABLED=false — pool disabled");
    return;
  }
  logger.info(
    { size: config.BOT_POOL_SIZE, idleAgeMs: config.BOT_POOL_MAX_IDLE_AGE_MS },
    "Starting bot pool"
  );
  try {
    await ensureImage();
  } catch (err: any) {
    logger.warn(
      { err: err.message },
      "pool: bot image not available — pool will retry on top-up cycle"
    );
  }

  // Restart recovery — reattach to existing pool-member containers.
  try {
    const existing = await listBotContainers();
    for (const info of existing) {
      if (info.Labels?.["bot.pool_member"] !== "true") continue;
      if (info.State !== "running") {
        logger.info(
          { containerId: info.Id, state: info.State },
          "pool: removing non-running pool-member from prior instance"
        );
        try {
          await removeContainer(info.Id);
        } catch {
          /* best-effort */
        }
        continue;
      }
      // Cold-bot rows always have a baked-in bot.job_id label. Pool-member
      // containers with a label set didn't really happen — but be defensive.
      if (info.Labels?.["bot.job_id"]) continue;
      // If a Redis claim mapping exists, this is an in-flight meeting that
      // a prior orchestrator dispatched but never saw exit. The worker's
      // existing reconnect path (Check 2) will reattach via the Path 2
      // lookup we added to findRunningContainerForJob.
      const claimedJobId = await lookupJobIdByContainer(info.Id);
      if (claimedJobId) {
        logger.info(
          { containerId: info.Id, jobId: claimedJobId },
          "pool: skipping claimed pool member — worker will reconnect"
        );
        continue;
      }
      const hostPort = await getContainerHostPort(info.Id);
      if (!hostPort) {
        logger.warn(
          { containerId: info.Id },
          "pool: no host port on existing pool member — removing"
        );
        try {
          await removeContainer(info.Id);
        } catch {
          /* best-effort */
        }
        continue;
      }
      const entry: PoolEntry = {
        containerId: info.Id,
        hostPort,
        state: "warming",
        createdAt: Date.parse(info.Created.toString()) || Date.now(),
      };
      pool.push(entry);
      pollForReady(entry).catch(() => {
        entry.state = "broken";
      });
    }
  } catch (err: any) {
    logger.warn({ err: err.message }, "pool: restart recovery scan failed");
  }

  await topUpPool();
  reapTimer = setInterval(() => {
    reapAndTopUp().catch((err) =>
      logger.warn({ err: err.message }, "pool reap cycle failed")
    );
  }, 5000);
}

export function stopPool(): void {
  if (reapTimer) {
    clearInterval(reapTimer);
    reapTimer = null;
  }
}

/** Read-only snapshot for observability (e.g., a /metrics endpoint). */
export function poolSnapshot(): {
  size: number;
  warming: number;
  warm: number;
  broken: number;
} {
  return {
    size: pool.length,
    warming: pool.filter((e) => e.state === "warming").length,
    warm: pool.filter((e) => e.state === "warm").length,
    broken: pool.filter((e) => e.state === "broken").length,
  };
}
