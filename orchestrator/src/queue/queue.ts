import { Queue, JobsOptions } from "bullmq";
import config from "../config";
import { BotJobData } from "../types";

export const botQueue = new Queue<BotJobData>(config.QUEUE_NAME, {
  connection: { url: config.REDIS_URL },
  defaultJobOptions: {
    attempts: 2,
    backoff: { type: "exponential", delay: 5000 },
    removeOnComplete: { age: 3600 },
    removeOnFail: { age: 86400 },
  },
});

export async function addBotJob(data: BotJobData): Promise<string> {
  const opts: JobsOptions = {};

  // Support delayed/scheduled jobs
  if (data.scheduledAt) {
    const delay = new Date(data.scheduledAt).getTime() - Date.now();
    if (delay > 0) {
      opts.delay = delay;
    }
  }

  // Use jobId as the BullMQ job ID for deduplication
  opts.jobId = data.jobId;

  await botQueue.add("join-meeting", data, opts);
  return data.jobId;
}
