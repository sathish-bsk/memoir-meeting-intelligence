import Docker from "dockerode";
import config from "../config";
import { BotJobData } from "../types";
import { logger } from "../logger";
import { getBot } from "../services/bot-tracker";

const docker = new Docker({ socketPath: "/var/run/docker.sock" });

export async function ensureImage(): Promise<void> {
  const imageName = config.BOT_IMAGE;
  try {
    const image = docker.getImage(imageName);
    await image.inspect();
    logger.info({ image: imageName }, "Bot image exists");
  } catch (err: any) {
    if (err.statusCode === 404) {
      // Image not found — build from BotWorker directory
      const path = await import("path");
      const buildContext = path.resolve(__dirname, "../../../..");
      logger.info({ image: imageName, context: buildContext }, "Bot image not found, building...");
      const stream = await docker.buildImage(
        { context: buildContext, src: ["."] },
        { t: imageName }
      );
      // Wait for build to complete
      await new Promise<void>((resolve, reject) => {
        docker.modem.followProgress(stream, (err: any) => {
          if (err) reject(err);
          else resolve();
        });
      });
      logger.info({ image: imageName }, "Bot image built successfully");
    } else {
      throw err;
    }
  }
}

export async function ensureNetwork(): Promise<void> {
  const networks = await docker.listNetworks({
    filters: { name: [config.BOT_NETWORK] },
  });
  const exists = networks.some((n) => n.Name === config.BOT_NETWORK);
  if (!exists) {
    await docker.createNetwork({
      Name: config.BOT_NETWORK,
      Driver: "bridge",
    });
    logger.info({ network: config.BOT_NETWORK }, "Docker network created");
  }
}

export async function createBotContainer(
  jobData: BotJobData
): Promise<Docker.Container> {
  const backendUrl = `${config.ORCHESTRATOR_URL}`;
  const suffix = Date.now().toString(36);
  const containerName = `bot-${jobData.jobId}-${suffix}`;

  // Browser-mode bots run a persistent-context Chromium with the
  // tabCapture extension and an offscreen document — that's heavier than
  // system mode (PulseAudio + FFmpeg). Without more memory + SHM the
  // renderer gets starved and the audio thread misses deadlines, which
  // shows up as 10-160ms dropouts in the captured stream (validated
  // 2026-05-07). Browser-mode bots get at least 1.5 GB / 512 MB SHM.
  const audioCaptureMethod = process.env.AUDIO_CAPTURE_METHOD || "system";
  // Tier 1.5 — opt-in FLAC encoding for the merged audio upload. Bot's
  // storage.py reads this env to switch the merge codec to FLAC level-8
  // (lossless, ~50% size reduction). Default "wav" preserves the legacy
  // pcm_s16le path. See BotWorker/app/services/storage.py and
  // TranscriptionBase/processor.py:_run_preprocess for the input-extension
  // handling on the consumer side.
  const botworkerAudioFormat = process.env.BOTWORKER_AUDIO_FORMAT || "wav";
  const minBrowserMemory = 1536 * 1024 * 1024; // 1.5 GB
  const minBrowserShm = 512 * 1024 * 1024;     // 512 MB
  const memoryLimit =
    audioCaptureMethod === "browser"
      ? Math.max(config.BOT_MEMORY_LIMIT, minBrowserMemory)
      : config.BOT_MEMORY_LIMIT;
  const shmSize =
    audioCaptureMethod === "browser"
      ? Math.max(config.BOT_SHM_SIZE, minBrowserShm)
      : config.BOT_SHM_SIZE;

  const container = await docker.createContainer({
    Image: config.BOT_IMAGE,
    name: containerName,
    Env: [
      `BACKEND_URL=${backendUrl}`,
      `TRANSCRIPTION_URL=${config.TRANSCRIPTION_URL}`,
      `STORAGE_BACKEND=${config.STORAGE_BACKEND}`,
      `LOG_LEVEL=${config.LOG_LEVEL}`,
      `DOCKER=1`,
      `AUTO_EXIT=1`,
      // Forward the orchestrator's AUDIO_CAPTURE_METHOD to bots (default
      // "system"). Set to "browser" on the orchestrator process to use
      // the experimental tab-capture path. See
      // plan/browser-capture-implementation-plan.md.
      `AUDIO_CAPTURE_METHOD=${audioCaptureMethod}`,
      `BOTWORKER_AUDIO_FORMAT=${botworkerAudioFormat}`,
      // Phase 3 — authenticated session env. Forwarded only when set on
      // the orchestrator; default `guest` keeps today's behavior.
      `MEMOIR_BOT_AUTH_MODE=${config.MEMOIR_BOT_AUTH_MODE}`,
      `MEMOIR_BOT_INVITE_EMAIL=${config.MEMOIR_BOT_INVITE_EMAIL}`,
      `MEMOIR_BOT_GOOGLE_PROFILE_KEY=${config.MEMOIR_BOT_GOOGLE_PROFILE_KEY}`,
      `MEMOIR_BOT_MICROSOFT_PROFILE_KEY=${config.MEMOIR_BOT_MICROSOFT_PROFILE_KEY}`,
      // AWS / R2 creds + S3 config — needed for boto3 to fetch the
      // Chromium profile tarball from R2 inside the bot container.
      // S3_ENDPOINT_URL is what points boto3 at Cloudflare R2 instead
      // of real AWS S3; without it the bot would fall back to guest.
      `AWS_ACCESS_KEY_ID=${config.AWS_ACCESS_KEY_ID}`,
      `AWS_SECRET_ACCESS_KEY=${config.AWS_SECRET_ACCESS_KEY}`,
      `AWS_REGION=${config.AWS_REGION}`,
      `S3_ENDPOINT_URL=${config.S3_ENDPOINT_URL}`,
      `S3_BUCKET=${config.S3_BUCKET}`,
      `S3_REGION=${config.S3_REGION}`,
    ],
    Labels: {
      "bot.managed": "true",
      "bot.job_id": jobData.jobId,
      "bot.meeting_url": jobData.meetingUrl,
      "bot.audio_capture_method": audioCaptureMethod,
    },
    ExposedPorts: { "8000/tcp": {} },
    HostConfig: {
      Memory: memoryLimit,
      NanoCpus: config.BOT_CPU_LIMIT,
      ShmSize: shmSize,
      NetworkMode: config.BOT_NETWORK,
      ExtraHosts: ["host.docker.internal:host-gateway"],
      // HostIp "127.0.0.1" locks the published port to loopback only.
      // Without it, dockerode defaults to 0.0.0.0 (all host interfaces) which
      // exposes the bot's /join endpoint to anything on the EC2 network.
      // Defense-in-depth fix shipping with the pool work but applying equally
      // to cold bots — closes the hijack vector regardless of pool state.
      PortBindings: { "8000/tcp": [{ HostIp: "127.0.0.1", HostPort: "0" }] },
      Binds: [
        `${config.BOT_RECORDINGS_DIR}:/tmp/recordings`,
        `${config.BOT_RECORDINGS_DIR}/../screenshots:/tmp/screenshots`,
        // Phase 3 — shared cache for the Chromium profile tarball. Bot
        // checks /data/bot-profiles/cache/{provider}.etag against R2,
        // downloads only when stale. Mount RW so the first bot can
        // populate the cache; subsequent bots reuse it.
        `/data/bot-profiles:/data/bot-profiles`,
      ],
    },
  });

  logger.info(
    {
      jobId: jobData.jobId,
      audioCaptureMethod,
      memoryMB: Math.round(memoryLimit / (1024 * 1024)),
      shmMB: Math.round(shmSize / (1024 * 1024)),
    },
    "Bot container resource limits"
  );

  logger.info(
    { jobId: jobData.jobId, containerId: container.id },
    "Bot container created"
  );
  return container;
}

/**
 * Create a pool-member container — no job_id, BOT_POOL_MEMBER=1, AUTO_EXIT=0
 * so it stays alive until claimed and assigned a real meeting via /join.
 * Container resource limits (memory, CPU, shm) are identical to cold bots —
 * the plan deliberately does NOT raise them until the instance is upgraded.
 */
export async function createPoolBotContainer(): Promise<Docker.Container> {
  const backendUrl = `${config.ORCHESTRATOR_URL}`;
  const suffix = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const containerName = `bot-pool-${suffix}`;

  const audioCaptureMethod = process.env.AUDIO_CAPTURE_METHOD || "system";
  if (audioCaptureMethod === "browser") {
    // Browser mode requires launch_persistent_context(user_data_dir=...) per
    // meeting, which can't be reused. Pool support is system-mode-only.
    throw new Error("Pool members not supported with AUDIO_CAPTURE_METHOD=browser");
  }
  const botworkerAudioFormat = process.env.BOTWORKER_AUDIO_FORMAT || "wav";

  const container = await docker.createContainer({
    Image: config.BOT_IMAGE,
    name: containerName,
    Env: [
      `BACKEND_URL=${backendUrl}`,
      `TRANSCRIPTION_URL=${config.TRANSCRIPTION_URL}`,
      `STORAGE_BACKEND=${config.STORAGE_BACKEND}`,
      `LOG_LEVEL=${config.LOG_LEVEL}`,
      `DOCKER=1`,
      // AUTO_EXIT=0 — pool members don't exit until a real meeting is
      // assigned via /join, at which point auto_exit_after=true flips
      // settings.AUTO_EXIT on inside the bot.
      `AUTO_EXIT=0`,
      // Triggers the lifespan's auto-warm task in BotWorker — pre-launches
      // Chromium at about:blank so /ready flips green ~12-15 s after start.
      `BOT_POOL_MEMBER=1`,
      `AUDIO_CAPTURE_METHOD=${audioCaptureMethod}`,
      `BOTWORKER_AUDIO_FORMAT=${botworkerAudioFormat}`,
      // Phase 3 — same env passthrough for pool members so they upgrade
      // to authenticated context on claim.
      `MEMOIR_BOT_AUTH_MODE=${config.MEMOIR_BOT_AUTH_MODE}`,
      `MEMOIR_BOT_INVITE_EMAIL=${config.MEMOIR_BOT_INVITE_EMAIL}`,
      `MEMOIR_BOT_GOOGLE_PROFILE_KEY=${config.MEMOIR_BOT_GOOGLE_PROFILE_KEY}`,
      `MEMOIR_BOT_MICROSOFT_PROFILE_KEY=${config.MEMOIR_BOT_MICROSOFT_PROFILE_KEY}`,
      `AWS_ACCESS_KEY_ID=${config.AWS_ACCESS_KEY_ID}`,
      `AWS_SECRET_ACCESS_KEY=${config.AWS_SECRET_ACCESS_KEY}`,
      `AWS_REGION=${config.AWS_REGION}`,
    ],
    Labels: {
      "bot.managed": "true",
      // Empty bot.job_id at boot — claimed members are tracked via the
      // pool:claim:<containerId> Redis index in bot-tracker.ts because
      // Docker labels are immutable on running containers.
      "bot.job_id": "",
      "bot.pool_member": "true",
      "bot.audio_capture_method": audioCaptureMethod,
    },
    ExposedPorts: { "8000/tcp": {} },
    HostConfig: {
      Memory: config.BOT_MEMORY_LIMIT,
      NanoCpus: config.BOT_CPU_LIMIT,
      ShmSize: config.BOT_SHM_SIZE,
      NetworkMode: config.BOT_NETWORK,
      ExtraHosts: ["host.docker.internal:host-gateway"],
      PortBindings: { "8000/tcp": [{ HostIp: "127.0.0.1", HostPort: "0" }] },
      Binds: [
        `${config.BOT_RECORDINGS_DIR}:/tmp/recordings`,
        `${config.BOT_RECORDINGS_DIR}/../screenshots:/tmp/screenshots`,
        // Phase 3 — shared cache for the Chromium profile tarball. Bot
        // checks /data/bot-profiles/cache/{provider}.etag against R2,
        // downloads only when stale. Mount RW so the first bot can
        // populate the cache; subsequent bots reuse it.
        `/data/bot-profiles:/data/bot-profiles`,
      ],
    },
  });

  logger.info(
    { containerId: container.id, name: containerName },
    "Pool bot container created"
  );
  return container;
}

export async function startContainer(container: Docker.Container): Promise<void> {
  await container.start();
  logger.info({ containerId: container.id }, "Bot container started");
}

export async function stopContainer(
  containerId: string,
  timeout: number = config.CONTAINER_STOP_TIMEOUT_S
): Promise<void> {
  const container = docker.getContainer(containerId);
  try {
    await container.stop({ t: timeout });
    logger.info({ containerId }, "Bot container stopped");
  } catch (err: any) {
    if (err.statusCode === 304) {
      logger.info({ containerId }, "Container already stopped");
    } else {
      throw err;
    }
  }
}

export async function removeContainer(containerId: string): Promise<void> {
  const container = docker.getContainer(containerId);
  try {
    await container.remove({ force: true });
    logger.info({ containerId }, "Bot container removed");
  } catch (err: any) {
    if (err.statusCode === 404) {
      logger.info({ containerId }, "Container already removed");
    } else {
      throw err;
    }
  }
}

export async function getContainerIp(containerId: string): Promise<string | null> {
  const container = docker.getContainer(containerId);
  const info = await container.inspect();
  const networks = info.NetworkSettings.Networks;
  const net = networks[config.BOT_NETWORK];
  return net?.IPAddress || null;
}

export async function getContainerHostPort(
  containerId: string,
  containerPort: string = "8000/tcp"
): Promise<number | null> {
  const container = docker.getContainer(containerId);
  const info = await container.inspect();
  const portBindings = info.NetworkSettings.Ports?.[containerPort];
  if (portBindings && portBindings.length > 0) {
    return parseInt(portBindings[0].HostPort, 10);
  }
  return null;
}

export async function getContainerLogs(
  containerId: string,
  tail: number = 1000
): Promise<string> {
  const container = docker.getContainer(containerId);
  const logs = await container.logs({
    stdout: true,
    stderr: true,
    timestamps: true,
    tail,
  });
  // dockerode returns a Buffer; demux stdout/stderr
  return logs.toString("utf-8");
}

export async function waitForContainer(
  containerId: string
): Promise<{ StatusCode: number }> {
  const container = docker.getContainer(containerId);
  return container.wait();
}

export async function listBotContainers(): Promise<Docker.ContainerInfo[]> {
  return docker.listContainers({
    all: true,
    filters: { label: ["bot.managed=true"] },
  });
}

export async function findRunningContainerForJob(
  jobId: string
): Promise<{ containerId: string; ip: string } | null> {
  // Path 1: cold-start bots have bot.job_id baked into the label at create
  // time. Label filter matches them directly.
  const containers = await docker.listContainers({
    filters: {
      label: [`bot.job_id=${jobId}`, "bot.managed=true"],
      status: ["running"],
    },
  });
  if (containers.length > 0) {
    const containerId = containers[0].Id;
    const ip = await getContainerIp(containerId);
    if (ip) return { containerId, ip };
  }

  // Path 2: pool-claimed bots have empty bot.job_id (labels are immutable on
  // running containers). The claim wrote containerId into bot-tracker; use
  // that to inspect the container directly.
  const bot = await getBot(jobId);
  if (!bot?.containerId) return null;
  try {
    const info = await docker.getContainer(bot.containerId).inspect();
    if (info.State.Running) {
      const ip = await getContainerIp(bot.containerId);
      if (ip) return { containerId: bot.containerId, ip };
    }
  } catch (err: any) {
    if (err.statusCode !== 404) throw err;
  }
  return null;
}

export async function findExitedContainerForJob(
  jobId: string
): Promise<{ containerId: string; exitCode: number } | null> {
  // Path 1: cold-start (label-baked) lookup.
  const containers = await docker.listContainers({
    all: true,
    filters: {
      label: [`bot.job_id=${jobId}`, "bot.managed=true"],
      status: ["exited"],
    },
  });
  if (containers.length > 0) {
    const containerId = containers[0].Id;
    const container = docker.getContainer(containerId);
    const info = await container.inspect();
    return { containerId, exitCode: info.State.ExitCode };
  }

  // Path 2: pool-claimed bot — resolve via bot-tracker's containerId.
  const bot = await getBot(jobId);
  if (!bot?.containerId) return null;
  try {
    const info = await docker.getContainer(bot.containerId).inspect();
    if (!info.State.Running && info.State.Status === "exited") {
      return { containerId: bot.containerId, exitCode: info.State.ExitCode };
    }
  } catch (err: any) {
    if (err.statusCode !== 404) throw err;
  }
  return null;
}

export { docker };
