import { Pool } from "pg";
import config from "../config";
import { logger } from "../logger";

const dbUrl = process.env.DATABASE_URL || config.DATABASE_URL;
const needsSsl = dbUrl?.includes("rds.amazonaws.com") || dbUrl?.includes("sslmode=require");

// Strip sslmode from connection string — pg v8 treats sslmode=require as verify-full
// which rejects RDS self-signed certs. We handle SSL via the Pool ssl option instead.
const connectionString = dbUrl.replace(/[?&]sslmode=[^&]*/g, "");

const pool = new Pool({
  connectionString,
  max: 5,
  ssl: needsSsl ? { rejectUnauthorized: false } : undefined,
});

pool.on("error", (err) => {
  logger.error({ err: err.message }, "Unexpected PostgreSQL pool error");
});

export interface InsertTranscriptionJobParams {
  meetingId?: string;
  botJobId: string;
  params: Record<string, unknown>;
}

export async function insertTranscriptionJob(
  data: InsertTranscriptionJobParams
): Promise<string> {
  // Check for existing job to prevent duplicates (bot may send recording_complete twice)
  const existing = await pool.query(
    `SELECT id FROM transcription_jobs
     WHERE meeting_id = $1 AND bot_job_id = $2 AND operation_type = 1
     LIMIT 1`,
    [data.meetingId || null, data.botJobId]
  );
  if (existing.rows.length > 0) {
    const existingId = existing.rows[0].id;
    logger.info(
      { transcriptionJobId: existingId, botJobId: data.botJobId },
      "Transcription job already exists, skipping duplicate insert"
    );
    return existingId;
  }

  const result = await pool.query(
    `INSERT INTO transcription_jobs (meeting_id, bot_job_id, status, params)
     VALUES ($1, $2, 'waiting', $3)
     RETURNING id`,
    [data.meetingId || null, data.botJobId, JSON.stringify(data.params)]
  );
  const jobId = result.rows[0].id;
  logger.info(
    { transcriptionJobId: jobId, botJobId: data.botJobId },
    "Inserted transcription job"
  );
  return jobId;
}

export async function closePool(): Promise<void> {
  await pool.end();
}

export { pool };
