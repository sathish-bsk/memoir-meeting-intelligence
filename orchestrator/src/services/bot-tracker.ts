import Redis from "ioredis";
import config from "../config";
import { BotEvent, BotJobStatus, BotState } from "../types";
import { logger } from "../logger";

const redis = new Redis(config.REDIS_URL);

const BOT_KEY_PREFIX = "bot:";
const BOT_EVENTS_SUFFIX = ":events";
// Pool claim index: pool:claim:<containerId> -> <jobId>. Used because Docker
// labels are immutable on running containers, so claimed pool members keep
// their boot-time empty bot.job_id label. Reaper + lookup paths consult this
// index when the label is empty + bot.pool_member=true.
const POOL_CLAIM_PREFIX = "pool:claim:";

function botKey(jobId: string): string {
  return `${BOT_KEY_PREFIX}${jobId}`;
}

function eventsKey(jobId: string): string {
  return `${BOT_KEY_PREFIX}${jobId}${BOT_EVENTS_SUFFIX}`;
}

export async function createBot(
  jobId: string,
  data: {
    meetingUrl: string;
    botName: string;
    callbackUrl?: string;
    meetingId?: string;
  }
): Promise<BotState> {
  const state: BotState = {
    jobId,
    status: BotJobStatus.QUEUED,
    meetingUrl: data.meetingUrl,
    botName: data.botName,
    createdAt: new Date().toISOString(),
    callbackUrl: data.callbackUrl,
    meetingId: data.meetingId,
  };

  await redis.hset(botKey(jobId), serializeState(state));
  logger.info({ jobId }, "Bot state created");
  return state;
}

export async function updateBot(
  jobId: string,
  updates: Partial<BotState>
): Promise<void> {
  const key = botKey(jobId);
  const exists = await redis.exists(key);
  if (!exists) {
    logger.warn({ jobId }, "Attempted to update non-existent bot");
    return;
  }

  const serialized: Record<string, string> = {};
  for (const [k, v] of Object.entries(updates)) {
    if (v !== undefined) {
      serialized[k] = String(v);
    }
  }

  if (Object.keys(serialized).length > 0) {
    await redis.hset(key, serialized);
  }
}

export async function getBot(jobId: string): Promise<BotState | null> {
  const data = await redis.hgetall(botKey(jobId));
  if (!data || Object.keys(data).length === 0) return null;
  return deserializeState(data);
}

export async function listBots(status?: BotJobStatus): Promise<BotState[]> {
  const keys = await scanKeys(`${BOT_KEY_PREFIX}*`);
  // Filter out event keys
  const botKeys = keys.filter((k) => !k.endsWith(BOT_EVENTS_SUFFIX));

  const bots: BotState[] = [];
  for (const key of botKeys) {
    const data = await redis.hgetall(key);
    if (data && Object.keys(data).length > 0) {
      const state = deserializeState(data);
      if (!status || state.status === status) {
        bots.push(state);
      }
    }
  }

  return bots.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
  );
}

export async function addEvent(jobId: string, event: BotEvent): Promise<void> {
  await redis.rpush(eventsKey(jobId), JSON.stringify(event));
}

export async function getEvents(jobId: string): Promise<BotEvent[]> {
  const items = await redis.lrange(eventsKey(jobId), 0, -1);
  return items.map((item) => JSON.parse(item));
}

/**
 * Record a pool claim: containerId -> jobId mapping in Redis.
 *
 * Called by pool-manager.claimWarmBot after marking an entry "claimed" and
 * before returning it to the worker. Also writes containerId into the bot
 * hash so findRunningContainerForJob's Path 2 fallback can resolve.
 */
export async function registerClaim(
  containerId: string,
  jobId: string
): Promise<void> {
  await redis.set(`${POOL_CLAIM_PREFIX}${containerId}`, jobId);
  // Mirror into the bot record so manager.ts findRunningContainerForJob
  // Path 2 can resolve via getBot(jobId).containerId.
  await updateBot(jobId, { containerId });
  logger.info({ containerId, jobId }, "Pool claim registered");
}

/**
 * Reverse lookup: given a containerId (e.g., from a Docker label scan that
 * found a pool member with empty bot.job_id), return the jobId it was
 * claimed by, or null if unclaimed.
 */
export async function lookupJobIdByContainer(
  containerId: string
): Promise<string | null> {
  return await redis.get(`${POOL_CLAIM_PREFIX}${containerId}`);
}

/**
 * Clear the claim index entry. Called from worker.ts handleContainerExit
 * after removeContainer so the index doesn't leak across orchestrator
 * restarts.
 */
export async function deregisterClaim(containerId: string): Promise<void> {
  await redis.del(`${POOL_CLAIM_PREFIX}${containerId}`);
}

export async function deleteBotData(jobId: string): Promise<void> {
  await redis.del(botKey(jobId), eventsKey(jobId));
  logger.info({ jobId }, "Bot data deleted");
}

async function scanKeys(pattern: string): Promise<string[]> {
  const keys: string[] = [];
  let cursor = "0";
  do {
    const [nextCursor, batch] = await redis.scan(
      cursor,
      "MATCH",
      pattern,
      "COUNT",
      100
    );
    cursor = nextCursor;
    keys.push(...batch);
  } while (cursor !== "0");
  return keys;
}

function serializeState(state: BotState): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [k, v] of Object.entries(state)) {
    if (v !== undefined && v !== null) {
      result[k] = String(v);
    }
  }
  return result;
}

function deserializeState(data: Record<string, string>): BotState {
  return {
    jobId: data.jobId,
    status: data.status as BotJobStatus,
    meetingUrl: data.meetingUrl,
    botName: data.botName,
    containerId: data.containerId || undefined,
    containerIp: data.containerIp || undefined,
    createdAt: data.createdAt,
    startedAt: data.startedAt || undefined,
    joinedAt: data.joinedAt || undefined,
    endedAt: data.endedAt || undefined,
    lastHeartbeat: data.lastHeartbeat || undefined,
    endReason: data.endReason || undefined,
    error: data.error || undefined,
    callbackUrl: data.callbackUrl || undefined,
    meetingId: data.meetingId || undefined,
  };
}

export { redis };
