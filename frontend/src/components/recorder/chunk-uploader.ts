/**
 * Chunked WebM uploader for the in-browser web recorder.
 *
 * Mirrors the Chrome extension's offscreen.ts chunk-flush pattern: a
 * MediaRecorder produces audio chunks every FLUSH_INTERVAL_MS, each
 * chunk gets a monotonic seq number, and chunks upload independently
 * via PUT .../chunks/{seq} to the existing tab-audio endpoints.
 *
 * Failure handling:
 *   - Per-chunk: retry up to 3 times with exponential backoff
 *   - Persistent: after 3 consecutive flush failures, surface to UI as
 *     a recoverable error (user can stop + finalize, which falls back
 *     to fallback-complete with whatever was collected client-side)
 *
 * Does NOT include the fallback-complete path itself — the recorder
 * component triggers that path when normal /complete fails on stop.
 */

const FLUSH_INTERVAL_MS = 30_000;
const MAX_CHUNK_RETRIES = 3;

export interface ChunkUploaderOptions {
  meetingId: string;
  sessionId: string;
  apiBase?: string;
  /** Token-getter callback. Called fresh before every PUT so long
   * recordings survive 15-min JWT expiry — the host page is expected
   * to wire this to the auth store, which is updated by api.ts's 401
   * refresh flow on other in-flight requests. */
  getAccessToken: () => string | null;
  onStateChange?: (state: ChunkUploaderState) => void;
}

export type ChunkUploaderState =
  | { kind: "idle" }
  | { kind: "recording"; uploadedChunks: number; pendingChunks: number }
  | { kind: "stopping"; uploadedChunks: number; pendingChunks: number }
  | { kind: "stopped"; uploadedChunks: number }
  | { kind: "error"; message: string; uploadedChunks: number };

async function computeSha256(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export class ChunkUploader {
  private mediaRecorder: MediaRecorder | null = null;
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private seq = 0;
  private uploadedCount = 0;
  private pendingCount = 0;
  private startTime = 0;
  private currentChunkStartMs = 0;
  private state: ChunkUploaderState = { kind: "idle" };
  private buffers: Blob[] = [];
  // Used as the fallback-complete payload if /complete fails after stop
  private allChunkBlobs: Blob[] = [];

  constructor(private readonly opts: ChunkUploaderOptions) {}

  private setState(s: ChunkUploaderState) {
    this.state = s;
    this.opts.onStateChange?.(s);
  }

  /** Start recording the given MediaStream as chunked WebM uploads. */
  async start(stream: MediaStream, track: "mixed" | "tab" = "mixed") {
    const supportedMimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/webm";

    this.mediaRecorder = new MediaRecorder(stream, {
      mimeType: supportedMimeType,
      audioBitsPerSecond: 128_000,
    });

    this.startTime = Date.now();
    this.currentChunkStartMs = 0;

    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) {
        this.buffers.push(e.data);
      }
    };

    // Periodic flush: stop+start the recorder so we get a complete WebM
    // blob per chunk that the backend can FFmpeg-decode standalone.
    // (Without stop-start, all chunks except the first lack headers.)
    this.flushTimer = setInterval(() => {
      void this.flush(false, track);
    }, FLUSH_INTERVAL_MS);

    this.mediaRecorder.start();
    this.setState({ kind: "recording", uploadedChunks: 0, pendingChunks: 0 });
  }

  /** Flush accumulated buffers as one chunk. final=true on stop. */
  private async flush(final: boolean, track: "mixed" | "tab" = "mixed"): Promise<void> {
    if (!this.mediaRecorder) return;

    // Force MediaRecorder to emit current buffer
    if (this.mediaRecorder.state === "recording") {
      this.mediaRecorder.requestData();
      // Brief await so ondataavailable fires before we read buffers
      await new Promise((r) => setTimeout(r, 50));
      this.mediaRecorder.stop();
    }

    const drained = this.buffers;
    this.buffers = [];

    if (drained.length === 0) {
      if (!final) {
        // Restart recorder to keep capturing
        this.mediaRecorder.start();
      }
      return;
    }

    const chunkBlob = new Blob(drained, { type: this.mediaRecorder.mimeType });
    this.allChunkBlobs.push(chunkBlob);
    const seq = this.seq++;
    const nowMs = Date.now() - this.startTime;
    const startMs = this.currentChunkStartMs;
    this.currentChunkStartMs = nowMs;

    this.pendingCount++;
    this.setState({
      kind: this.state.kind === "stopping" ? "stopping" : "recording",
      uploadedChunks: this.uploadedCount,
      pendingChunks: this.pendingCount,
    });

    // Restart recorder if we're not done
    if (!final) {
      this.mediaRecorder.start();
    }

    // Upload in background with retry
    void this.uploadChunk(chunkBlob, seq, startMs, nowMs - startMs, track);
  }

  private async uploadChunk(
    blob: Blob,
    seq: number,
    startMs: number,
    durationMs: number,
    track: "mixed" | "tab",
  ): Promise<void> {
    const sha256 = await computeSha256(blob);

    for (let attempt = 0; attempt < MAX_CHUNK_RETRIES; attempt++) {
      try {
        const fd = new FormData();
        fd.append("file", blob, `chunk_${seq}.webm`);
        fd.append("sha256", sha256);
        fd.append("start_ms", String(startMs));
        fd.append("duration_ms", String(durationMs));
        fd.append("size", String(blob.size));
        fd.append("track", track);

        const apiBase = this.opts.apiBase ?? "";
        const url = `${apiBase}/api/meetings/${this.opts.meetingId}/tab-audio/sessions/${this.opts.sessionId}/chunks/${seq}`;
        // Fresh token read per PUT — the auth store may have rotated
        // since recording started (15-min JWT TTL).
        const token = this.opts.getAccessToken();
        const res = await fetch(url, {
          method: "PUT",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: fd,
        });

        if (res.ok) {
          this.pendingCount--;
          this.uploadedCount++;
          if (this.state.kind === "recording" || this.state.kind === "stopping") {
            this.setState({
              ...this.state,
              uploadedChunks: this.uploadedCount,
              pendingChunks: this.pendingCount,
            });
          }
          return;
        }
        if (res.status === 409) {
          // Chunk already uploaded — treat as success (server-side idempotency)
          this.pendingCount--;
          this.uploadedCount++;
          return;
        }
        // Non-retryable client errors: don't burn the retry budget.
        // 401/403 are real auth failures; 400/404/413 mean the request
        // is malformed or oversized — retrying won't help.
        if (res.status === 400 || res.status === 401 || res.status === 403 || res.status === 404 || res.status === 413) {
          this.setState({
            kind: "error",
            message: `Chunk ${seq} rejected: HTTP ${res.status}`,
            uploadedChunks: this.uploadedCount,
          });
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      } catch (e) {
        if (attempt === MAX_CHUNK_RETRIES - 1) {
          this.setState({
            kind: "error",
            message: `Chunk ${seq} failed after ${MAX_CHUNK_RETRIES} attempts: ${e}`,
            uploadedChunks: this.uploadedCount,
          });
          return;
        }
        // Exponential backoff: 500ms, 1500ms, 4500ms
        await new Promise((r) => setTimeout(r, 500 * Math.pow(3, attempt)));
      }
    }
  }

  /** Stop the recorder, flush final chunk, wait for all uploads to settle. */
  async stop(track: "mixed" | "tab" = "mixed"): Promise<{ uploadedChunks: number; pendingChunks: number; allBlobs: Blob[] }> {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }

    this.setState({
      kind: "stopping",
      uploadedChunks: this.uploadedCount,
      pendingChunks: this.pendingCount,
    });

    // Final flush
    await this.flush(true, track);

    // Wait briefly for in-flight uploads to settle (max 15 s)
    const deadline = Date.now() + 15_000;
    while (this.pendingCount > 0 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 200));
    }

    this.setState({ kind: "stopped", uploadedChunks: this.uploadedCount });
    return {
      uploadedChunks: this.uploadedCount,
      pendingChunks: this.pendingCount,
      allBlobs: this.allChunkBlobs,
    };
  }

  /** Cancel without waiting for in-flight uploads. */
  cancel(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      try {
        this.mediaRecorder.stop();
      } catch {
        /* ignore */
      }
    }
    this.mediaRecorder = null;
  }
}
