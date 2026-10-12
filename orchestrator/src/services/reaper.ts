import config from "../config";
import { BotJobStatus } from "../types";
import { listBotContainers, stopContainer, removeContainer } from "../docker/manager";
import { getBot, updateBot, listBots, lookupJobIdByContainer } from "./bot-tracker";
import { forwardEvent } from "./event-forwarder";
import { logger } from "../logger";

let reaperInterval: ReturnType<typeof setInterval> | null = null;

export function startReaper(): void {
  reaperInterval = setInterval(reapZombies, config.ZOMBIE_REAPER_INTERVAL_MS);
  logger.info(
    { intervalMs: config.ZOMBIE_REAPER_INTERVAL_MS },
    "Zombie reaper started"
  );
}

export function stopReaper(): void {
  if (reaperInterval) {
    clearInterval(reaperInterval);
    reaperInterval = null;
    logger.info("Zombie reaper stopped");
  }
}

async function reapZombies(): Promise<void> {
  try {
    const containers = await listBotContainers();
    const runningContainers = containers.filter(
      (c) => c.State === "running"
    );

    for (const containerInfo of runningContainers) {
      let jobId = containerInfo.Labels?.["bot.job_id"] || null;

      // Pool members boot with an empty bot.job_id label (Docker doesn't
      // allow label edits on running containers). Claims are tracked in
      // Redis. If the label is empty AND this container is a pool member,
      // resolve via lookupJobIdByContainer. If it's empty and NOT a pool
      // member, it's a truly orphaned container — skip; a separate
      // cleanup path can handle it.
      if (!jobId) {
        if (containerInfo.Labels?.["bot.pool_member"] === "true") {
          jobId = await lookupJobIdByContainer(containerInfo.Id);
          // Unclaimed pool members are owned by pool-manager (which has
          // its own broken-state reaper + idle-age recycler). Don't
          // touch them here.
          if (!jobId) continue;
        } else {
          continue;
        }
      }

      const bot = await getBot(jobId);
      if (!bot) {
        // Orphaned container — no Redis state
        logger.warn({ jobId, containerId: containerInfo.Id }, "Orphaned container found, removing");
        await safeStopAndRemove(containerInfo.Id);
        continue;
      }

      // Skip containers in terminal states (they should be cleaning up)
      const terminalStates = [
        BotJobStatus.COMPLETED,
        BotJobStatus.FAILED,
        BotJobStatus.KILLED,
      ];
      if (terminalStates.includes(bot.status)) continue;

      let zombieReason: string | null = null;

      // Check heartbeat timeout
      if (bot.lastHeartbeat) {
        const lastBeat = new Date(bot.lastHeartbeat).getTime();
        const elapsed = Date.now() - lastBeat;
        if (elapsed > config.HEARTBEAT_TIMEOUT_MS) {
          zombieReason = `heartbeat_timeout (last: ${Math.round(elapsed / 1000)}s ago)`;
        }
      }

      // Check max meeting duration
      const createdAt = new Date(bot.createdAt).getTime();
      const age = Date.now() - createdAt;
      if (age > config.MAX_MEETING_DURATION_MS) {
        zombieReason = `max_duration_exceeded (age: ${Math.round(age / 1000)}s)`;
      }

      if (zombieReason) {
        logger.warn(
          { jobId, containerId: containerInfo.Id, reason: zombieReason },
          "Zombie container detected, killing"
        );

        await safeStopAndRemove(containerInfo.Id);
        await updateBot(jobId, {
          status: BotJobStatus.KILLED,
          endedAt: new Date().toISOString(),
          endReason: zombieReason,
        });

        await forwardEvent({
          jobId,
          eventType: "BOT_KILLED",
          timestamp: new Date().toISOString(),
          data: { reason: zombieReason },
        });
      }
    }
  } catch (err) {
    logger.error({ err }, "Reaper cycle failed");
  }
}

async function safeStopAndRemove(containerId: string): Promise<void> {
  try {
    await stopContainer(containerId, config.CONTAINER_STOP_TIMEOUT_S);
  } catch (err) {
    logger.error({ err, containerId }, "Failed to stop container");
  }
  try {
    await removeContainer(containerId);
  } catch (err) {
    logger.error({ err, containerId }, "Failed to remove container");
  }
}
