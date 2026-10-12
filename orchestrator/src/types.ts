export enum BotJobStatus {
  QUEUED = "queued",
  CREATING = "creating",
  STARTING = "starting",
  JOINING = "joining",
  RECORDING = "recording",
  PROCESSING = "processing",
  COMPLETED = "completed",
  FAILED = "failed",
  KILLED = "killed",
}

export interface BotJobData {
  jobId: string;
  meetingUrl: string;
  botName?: string;
  callbackUrl?: string;
  scheduledAt?: string;
}

export interface BotEvent {
  jobId: string;
  eventType: string;
  timestamp: string;
  data?: Record<string, unknown>;
}

export interface BotState {
  jobId: string;
  status: BotJobStatus;
  meetingUrl: string;
  botName: string;
  containerId?: string;
  containerIp?: string;
  createdAt: string;
  startedAt?: string;
  joinedAt?: string;
  endedAt?: string;
  lastHeartbeat?: string;
  endReason?: string;
  error?: string;
  callbackUrl?: string;
  meetingId?: string;
}
