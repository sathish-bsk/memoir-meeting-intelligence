import { z } from "zod";

const meetingUrlPattern = /^https:\/\/(meet\.google\.com|[\w.-]*zoom\.us|teams\.microsoft\.com|teams\.live\.com)\//;

export const createBotSchema = z.object({
  jobId: z.string().optional(),
  meetingUrl: z.string().url().regex(meetingUrlPattern, "Must be a Google Meet, Zoom, or Teams URL"),
  botName: z.string().optional(),
  callbackUrl: z.string().url().optional(),
  scheduledAt: z.string().datetime().optional(),
  meetingId: z.string().optional(),
});

export const stopBotSchema = z.object({
  reason: z.string().optional().default("manual_stop"),
});

export const botEventSchema = z.object({
  event_type: z.string(),
  job_id: z.string(),
  timestamp: z.string(),
  data: z.record(z.unknown()).optional(),
});

export type CreateBotRequest = z.infer<typeof createBotSchema>;
export type StopBotRequest = z.infer<typeof stopBotSchema>;
export type BotEventRequest = z.infer<typeof botEventSchema>;
