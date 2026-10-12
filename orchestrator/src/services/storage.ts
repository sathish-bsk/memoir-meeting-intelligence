/**
 * S3/R2 storage helper for the orchestrator.
 * Uploads bot recording files to cloud storage when STORAGE_BACKEND=s3.
 */
import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import { readFileSync, readdirSync, statSync } from "fs";
import { rm } from "fs/promises";
import path from "path";
import config from "../config";
import { logger } from "../logger";

let s3Client: S3Client | null = null;

function getS3Client(): S3Client {
  if (!s3Client) {
    s3Client = new S3Client({
      region: config.S3_REGION,
      endpoint: config.S3_ENDPOINT_URL,
      credentials: {
        accessKeyId: config.AWS_ACCESS_KEY_ID,
        secretAccessKey: config.AWS_SECRET_ACCESS_KEY,
      },
      forcePathStyle: true,
    });
  }
  return s3Client;
}

export function isS3(): boolean {
  return config.STORAGE_BACKEND === "s3";
}

/**
 * Upload a single file to S3/R2.
 */
export async function uploadFile(localPath: string, s3Key: string): Promise<void> {
  const client = getS3Client();
  const body = readFileSync(localPath);

  await client.send(
    new PutObjectCommand({
      Bucket: config.S3_BUCKET,
      Key: s3Key,
      Body: body,
    })
  );

  logger.debug({ key: s3Key, size: body.length }, "Uploaded file to R2");
}

/**
 * Upload all files in a directory to S3/R2, preserving relative paths under a prefix.
 * E.g. uploadDirectory("/data/recordings/job123", "job123") uploads all files
 * in job123/ as job123/merged.wav, job123/speaker_events.json, etc.
 */
export async function uploadDirectory(dirPath: string, s3Prefix: string): Promise<void> {
  const entries = readdirSync(dirPath);

  for (const entry of entries) {
    const fullPath = path.join(dirPath, entry);
    const stat = statSync(fullPath);

    if (stat.isFile()) {
      const key = `${s3Prefix}/${entry}`;
      await uploadFile(fullPath, key);
    } else if (stat.isDirectory()) {
      await uploadDirectory(fullPath, `${s3Prefix}/${entry}`);
    }
  }
}

/**
 * Delete a local directory recursively.
 */
export async function deleteLocalDir(dirPath: string): Promise<void> {
  try {
    await rm(dirPath, { recursive: true, force: true });
    logger.debug({ dir: dirPath }, "Deleted local directory");
  } catch (err: any) {
    logger.warn({ dir: dirPath, err: err.message }, "Failed to delete local directory");
  }
}
