import { BotEvent } from "../types";
import { getBot } from "./bot-tracker";
import config from "../config";
import { logger } from "../logger";

const MAX_RETRIES = 3;
const BACKOFF_BASE_MS = 1000;
// Per-attempt fetch timeout. 5 s was too aggressive — a cold core_api
// pool + WAL flush + first cascade can legitimately take 2-6 s, which
// crossed the deadline and triggered the retry storm we kept hitting
// (duplicate webhooks pile-up → row lock contention → 14 s pool holds).
// 20 s gives the receiver plenty of headroom for any reasonably-slow
// handler, while still bounding worst-case retry budget to ~67 s
// (20 + 1 + 20 + 2 + 20 + 4) which is acceptable for non-blocking
// fire-and-forget event forwarding. Combined with the shared Redis
// client in core_api (no per-call TCP handshake), happy-path handlers
// now finish well under 200 ms so this ceiling is rarely exercised.
const FORWARD_TIMEOUT_MS = 20000;

export async function forwardEvent(event: BotEvent): Promise<void> {
  const bot = await getBot(event.jobId);

  // Forward to job-specific callback URL (if set) and global backend callback
  const urls: string[] = [];
  if (bot?.callbackUrl) urls.push(bot.callbackUrl);
  if (config.BACKEND_CALLBACK_URL && !urls.includes(config.BACKEND_CALLBACK_URL)) urls.push(config.BACKEND_CALLBACK_URL);

  if (urls.length === 0) return;

  // Forward to all URLs concurrently, fire-and-forget
  for (const url of urls) {
    forwardToUrl(url, event).catch((err) => {
      logger.error(
        { err, jobId: event.jobId, url },
        "Event forwarding failed after all retries"
      );
    });
  }
}

async function forwardToUrl(url: string, event: BotEvent): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (config.INTERNAL_SERVICE_KEY) {
    headers["X-Service-Key"] = config.INTERNAL_SERVICE_KEY;
  }

  const _t_forward_start = process.hrtime.bigint();

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const _t_attempt_start = process.hrtime.bigint();
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(event),
        signal: AbortSignal.timeout(FORWARD_TIMEOUT_MS),
      });

      const _t_attempt_end = process.hrtime.bigint();
      const _attempt_ms = Number(_t_attempt_end - _t_attempt_start) / 1_000_000;
      const _total_ms = Number(_t_attempt_end - _t_forward_start) / 1_000_000;
      if (resp.ok) {
        // INFO log (not debug) for key events so we can trace the full
        // chain. Filtered to high-signal events to avoid log spam from
        // chunk_saved / heartbeat.
        if (["joined_meeting", "recording_started", "meeting_ended"].includes(event.eventType)) {
          logger.info({
            jobId: event.jobId,
            eventType: event.eventType,
            attempt: attempt + 1,
            attemptMs: Math.round(_attempt_ms),
            totalMs: Math.round(_total_ms),
          }, "[FORWARD_DONE] event forwarded to core_api");
        }
        return;
      }

      logger.warn(
        {
          jobId: event.jobId,
          eventType: event.eventType,
          status: resp.status,
          attempt: attempt + 1,
        },
        "Event forward got non-OK status"
      );
    } catch (err) {
      logger.warn(
        { jobId: event.jobId, attempt: attempt + 1, err },
        "Event forward failed"
      );
    }

    if (attempt < MAX_RETRIES - 1) {
      await sleep(BACKOFF_BASE_MS * Math.pow(2, attempt));
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
