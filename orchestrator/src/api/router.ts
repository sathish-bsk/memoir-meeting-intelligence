import { Router, Request, Response } from "express";
import { v4 as uuidv4 } from "uuid";
import { createBotSchema, botEventSchema } from "./validation";
import { addBotJob } from "../queue/queue";
import { BotJobStatus, BotEvent } from "../types";
import config from "../config";
import {
  createBot,
  updateBot,
  getBot,
  listBots,
  addEvent,
  getEvents,
} from "../services/bot-tracker";
import { stopContainer, removeContainer } from "../docker/manager";
import { forwardEvent } from "../services/event-forwarder";
import { insertTranscriptionJob } from "../services/db";
import { isS3, uploadDirectory } from "../services/storage";
import { logger } from "../logger";
import path from "path";

const router = Router();

// Health check
router.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", service: "orchestrator" });
});

// Create a new bot job
router.post("/api/bots", async (req: Request, res: Response) => {
  try {
    const parsed = createBotSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({
        error: "Validation error",
        details: parsed.error.issues,
      });
      return;
    }

    const { meetingUrl, botName, callbackUrl, scheduledAt, meetingId } = parsed.data;
    const jobId = parsed.data.jobId || uuidv4();

    // Check for duplicate
    const existing = await getBot(jobId);
    if (existing) {
      res.status(409).json({
        error: "Job already exists",
        jobId,
        status: existing.status,
      });
      return;
    }

    // Create state in Redis
    await createBot(jobId, {
      meetingUrl,
      botName: botName || "Meeting Notetaker",
      callbackUrl,
      meetingId,
    });

    // Enqueue job
    await addBotJob({ jobId, meetingUrl, botName, callbackUrl, scheduledAt });

    logger.info({ jobId, meetingUrl }, "Bot job created");
    res.status(201).json({ jobId, status: BotJobStatus.QUEUED });
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to create bot job");
    res.status(500).json({ error: "Internal server error" });
  }
});

// List all bots
router.get("/api/bots", async (req: Request, res: Response) => {
  try {
    const status = req.query.status as BotJobStatus | undefined;
    const bots = await listBots(status);
    res.json(
      bots.map((b) => ({
        jobId: b.jobId,
        status: b.status,
        meetingUrl: b.meetingUrl,
        botName: b.botName,
        containerId: b.containerId,
        createdAt: b.createdAt,
        startedAt: b.startedAt,
        endedAt: b.endedAt,
      }))
    );
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to list bots");
    res.status(500).json({ error: "Internal server error" });
  }
});

// Get single bot status
router.get("/api/bots/:jobId", async (req: Request, res: Response) => {
  try {
    const bot = await getBot(req.params.jobId);
    if (!bot) {
      res.status(404).json({ error: "Bot not found" });
      return;
    }

    const events = await getEvents(req.params.jobId);
    res.json({ ...bot, events });
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to get bot");
    res.status(500).json({ error: "Internal server error" });
  }
});

// Stop a bot
router.delete("/api/bots/:jobId", async (req: Request, res: Response) => {
  try {
    const bot = await getBot(req.params.jobId);
    if (!bot) {
      res.status(404).json({ error: "Bot not found" });
      return;
    }

    const terminalStates = [
      BotJobStatus.COMPLETED,
      BotJobStatus.FAILED,
      BotJobStatus.KILLED,
    ];
    if (terminalStates.includes(bot.status)) {
      res.json({ jobId: bot.jobId, status: bot.status, message: "Already stopped" });
      return;
    }

    // Send /stop to the bot container (non-blocking for the response)
    if (bot.containerIp) {
      try {
        await fetch(`http://${bot.containerIp}/stop`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "manual_stop" }),
          signal: AbortSignal.timeout(5000),
        });
        logger.info({ jobId: bot.jobId }, "Sent /stop to bot container");
      } catch {
        logger.warn({ jobId: bot.jobId }, "Could not reach bot /stop, forcing container stop");
      }
    }

    // Update status immediately so the API returns fast
    await updateBot(req.params.jobId, {
      status: BotJobStatus.KILLED,
      endedAt: new Date().toISOString(),
      endReason: "manual_stop",
    });

    logger.info({ jobId: bot.jobId }, "Bot stop initiated");
    res.json({ jobId: bot.jobId, status: BotJobStatus.KILLED });

    // Schedule container cleanup in background — give bot time to:
    //   (1) leave the meeting gracefully (~1s click Leave),
    //   (2) merge per-chunk WAVs via FFmpeg (~5-15s for 60+ chunks),
    //   (3) apply silenceremove + dither + downmix (~5-10s),
    //   (4) upload ~180 MB merged WAV to R2 (10-30s on EC2 outbound),
    //   (5) fire recording_complete webhook (~1s).
    // Total worst case ~60s on 30-min recordings. Previously this was
    // 15s setTimeout + stopContainer(t:10) = ~25s — too short, causing
    // long recordings to lose the merged file mid-upload (see F3 / merge
    // succeeded but no recording_complete event). Bumped to 90s+30s=120s.
    // Override via env var BOT_STOP_GRACE_MS / BOT_STOP_SIGKILL_TIMEOUT_S
    // for tight resource scenarios.
    if (bot.containerId) {
      const graceMs = parseInt(process.env.BOT_STOP_GRACE_MS || "90000", 10);
      const sigkillTimeoutS = parseInt(process.env.BOT_STOP_SIGKILL_TIMEOUT_S || "30", 10);
      setTimeout(async () => {
        try {
          await stopContainer(bot.containerId!, sigkillTimeoutS);
          logger.info({ jobId: bot.jobId, graceMs, sigkillTimeoutS }, "Container force-stopped after grace period");
        } catch {
          // Container may have already exited via AUTO_EXIT
        }
      }, graceMs);
    }
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to stop bot");
    res.status(500).json({ error: "Internal server error" });
  }
});

// Receive events from bot containers
router.post("/api/bots/events", async (req: Request, res: Response) => {
  const _t_entry = process.hrtime.bigint();
  try {
    const parsed = botEventSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({
        error: "Validation error",
        details: parsed.error.issues,
      });
      return;
    }

    const { job_id, event_type, timestamp, data } = parsed.data;
    // Log every event arrival so we can correlate with core_api's
    // "Webhook received" log line and measure orchestrator overhead.
    // INFO-level — pino will route normally.
    logger.info({
      jobId: job_id,
      eventType: event_type,
      botTimestamp: timestamp,
    }, "[BOT_EVENT_IN] received from bot");

    const event: BotEvent = {
      jobId: job_id,
      eventType: event_type,
      timestamp,
      data: data as Record<string, unknown>,
    };

    // Store event
    await addEvent(job_id, event);

    // Update bot state based on event type
    switch (event_type) {
      case "waiting_room_entered":
        await updateBot(job_id, {
          status: BotJobStatus.JOINING,
          lastHeartbeat: new Date().toISOString(),
        });
        break;
      case "joined_meeting":
        await updateBot(job_id, {
          status: BotJobStatus.RECORDING,
          joinedAt: timestamp,
          lastHeartbeat: new Date().toISOString(),
        });
        break;
      case "meeting_ended":
        await updateBot(job_id, {
          status: BotJobStatus.PROCESSING,
          lastHeartbeat: new Date().toISOString(),
          endReason: (data as any)?.reason,
        });
        break;
      case "recording_complete": {
        await updateBot(job_id, {
          status: BotJobStatus.COMPLETED,
          endedAt: new Date().toISOString(),
          lastHeartbeat: new Date().toISOString(),
        });
        const bot = await getBot(job_id);
        if (bot) {
          // Upload bot recording directory to R2 BEFORE creating the
          // transcription job — ensures merged.wav is available when
          // the worker picks up the job.
          if (isS3()) {
            try {
              const recordingDir = path.join(config.BOT_RECORDINGS_DIR, job_id);
              await uploadDirectory(recordingDir, job_id);
              logger.info({ jobId: job_id }, "Bot recording uploaded to R2");
            } catch (uploadErr: any) {
              logger.error({ err: uploadErr.message, jobId: job_id }, "Failed to upload recording to R2");
            }
          }

          // Insert transcription job into DB queue (after upload)
          try {
            await insertTranscriptionJob({
              meetingId: bot.meetingId,
              botJobId: job_id,
              params: {
                input_audio_path: (data as any)?.recording_path || `${job_id}/merged.wav`,
                output_base_path: bot.meetingId ? `meetings/${bot.meetingId}/v1/` : `meetings/unknown/v1/`,
                webhook_url: config.TRANSCRIPTION_WEBHOOK_URL || bot.callbackUrl || config.BACKEND_CALLBACK_URL,
                webhook_headers: config.INTERNAL_SERVICE_KEY
                  ? { "X-Service-Key": config.INTERNAL_SERVICE_KEY }
                  : {},
                engine: "faster-whisper",
                model_size: "medium",
                processing_mode: "path_b",
                phone_mode: true,
                multilingual: true,
                diarization: true,
                vocab_correction: true,
                llm_correction: true,
                enable_intelligence: true,
                post_processing_type: "memoir",
                max_retries: 3,
              },
            });
          } catch (dbErr: any) {
            logger.error({ err: dbErr.message, jobId: job_id }, "Failed to insert transcription job");
          }
        }
        break;
      }
      case "transcription_complete":
        await updateBot(job_id, {
          status: BotJobStatus.COMPLETED,
          endedAt: new Date().toISOString(),
          lastHeartbeat: new Date().toISOString(),
        });
        // Local cleanup handled by core_api webhook after TranscriptionBase is truly done
        break;
      case "transcription_failed":
      case "bot_error":
      case "bot_denied":
        await updateBot(job_id, {
          status: BotJobStatus.FAILED,
          endedAt: new Date().toISOString(),
          error: (data as any)?.error || event_type,
          lastHeartbeat: new Date().toISOString(),
        });
        break;
      case "heartbeat":
        await updateBot(job_id, {
          lastHeartbeat: new Date().toISOString(),
        });
        break;
      default:
        await updateBot(job_id, {
          lastHeartbeat: new Date().toISOString(),
        });
    }

    // Forward event to backend
    forwardEvent(event).catch(() => {});

    const _t_done = process.hrtime.bigint();
    const _elapsed_ms = Number(_t_done - _t_entry) / 1_000_000;
    if (_elapsed_ms > 100 || ["joined_meeting", "recording_started", "meeting_ended"].includes(event_type)) {
      logger.info({
        jobId: job_id,
        eventType: event_type,
        elapsedMs: Math.round(_elapsed_ms),
      }, "[BOT_EVENT_HANDLED] handler done (forward fired bg)");
    }

    res.json({ received: true });
  } catch (err: any) {
    logger.error({ err: err.message }, "Failed to process bot event");
    res.status(500).json({ error: "Internal server error" });
  }
});

export default router;
