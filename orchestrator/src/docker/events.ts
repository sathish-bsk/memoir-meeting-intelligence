import { docker } from "./manager";
import { BotJobStatus } from "../types";
import { getBot, updateBot } from "../services/bot-tracker";
import { forwardEvent } from "../services/event-forwarder";
import { logger } from "../logger";

export async function startDockerEventListener(): Promise<void> {
  const stream = await docker.getEvents({
    filters: {
      type: ["container"],
      event: ["die", "oom", "kill"],
      label: ["bot.managed=true"],
    },
  });

  stream.on("data", async (chunk: Buffer) => {
    try {
      const event = JSON.parse(chunk.toString());
      const exitCode = event.Actor?.Attributes?.exitCode;
      const jobId = event.Actor?.Attributes?.["bot.job_id"];

      if (!jobId) return;

      logger.info(
        { jobId, event: event.Action, exitCode },
        "Docker event received"
      );

      const bot = await getBot(jobId);
      if (!bot) return;

      // Only update if not already in a terminal state
      const terminalStates = [
        BotJobStatus.COMPLETED,
        BotJobStatus.FAILED,
        BotJobStatus.KILLED,
      ];
      if (terminalStates.includes(bot.status)) return;

      if (event.Action === "die") {
        const status =
          exitCode === "0" ? BotJobStatus.COMPLETED : BotJobStatus.FAILED;
        await updateBot(jobId, {
          status,
          endedAt: new Date().toISOString(),
          error:
            exitCode !== "0"
              ? `Container exited with code ${exitCode}`
              : undefined,
        });
        logger.info(
          { jobId, exitCode, status },
          "Bot container exited (via Docker event)"
        );

        // Forward failure to core_api so meeting status gets updated
        if (exitCode !== "0") {
          await forwardEvent({
            jobId,
            eventType: "bot_error",
            timestamp: new Date().toISOString(),
            data: { error: `Container exited with code ${exitCode}` },
          });
        }
      } else if (event.Action === "oom") {
        await updateBot(jobId, {
          status: BotJobStatus.FAILED,
          endedAt: new Date().toISOString(),
          error: "Container killed by OOM",
        });
        logger.error({ jobId }, "Bot container OOM killed");

        await forwardEvent({
          jobId,
          eventType: "bot_error",
          timestamp: new Date().toISOString(),
          data: { error: "Container killed by OOM" },
        });
      }
    } catch (err) {
      logger.error({ err }, "Error processing Docker event");
    }
  });

  stream.on("error", (err: Error) => {
    logger.error({ err }, "Docker event stream error");
  });

  logger.info("Docker event listener started");
}
