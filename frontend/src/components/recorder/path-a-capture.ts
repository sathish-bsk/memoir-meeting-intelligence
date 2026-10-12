/**
 * Path A (no-extension) capture via getDisplayMedia + getUserMedia.
 *
 * Browser security prevents us from reading the picked tab's URL or DOM,
 * so this path cannot scrape participants or captions. Audio + diarization
 * only — the rich attribution work requires the extension (Path B).
 *
 * Caller is expected to:
 *   1. Call requestMixedCapture()
 *   2. Pass the returned stream to a ChunkUploader
 *   3. Call stopMixedCapture(stream) on stop / cancel
 */

export class CaptureError extends Error {
  constructor(
    public readonly kind: "permission_denied" | "no_audio" | "not_supported" | "user_cancelled",
    message: string,
  ) {
    super(message);
    this.name = "CaptureError";
  }
}

/**
 * Request a mixed audio stream: tab audio (from getDisplayMedia) + mic
 * (from getUserMedia), combined via Web Audio API into a single
 * MediaStream suitable for MediaRecorder.
 *
 * The user is prompted twice:
 *   - First: getDisplayMedia tab/window picker (must tick "Share audio")
 *   - Second: getUserMedia mic permission (if not already granted)
 */
export async function requestMixedCapture(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    throw new CaptureError("not_supported", "Browser does not support tab audio capture");
  }

  let display: MediaStream;
  try {
    display = await navigator.mediaDevices.getDisplayMedia({
      audio: true,
      video: { displaySurface: "browser" } as MediaTrackConstraints,
      // Hint Chrome to default the picker to "Chrome Tab"
      // (some Chrome versions respect this, others ignore)
      preferCurrentTab: false,
    } as DisplayMediaStreamOptions);
  } catch (e) {
    const msg = (e as Error).message || String(e);
    if (msg.toLowerCase().includes("denied") || msg.toLowerCase().includes("permission")) {
      throw new CaptureError("permission_denied", msg);
    }
    // getDisplayMedia throws NotAllowedError when the user closes the picker
    throw new CaptureError("user_cancelled", msg);
  }

  // Validate that the user actually shared audio (didn't tick "Share audio")
  const tabAudioTracks = display.getAudioTracks();
  if (tabAudioTracks.length === 0) {
    // Stop the video track we got but don't need
    display.getVideoTracks().forEach((t) => t.stop());
    throw new CaptureError(
      "no_audio",
      "Tab audio was not shared. Please retry and tick 'Share audio' in the picker.",
    );
  }

  // Drop the video track immediately — we only need audio
  display.getVideoTracks().forEach((t) => t.stop());

  let mic: MediaStream | null = null;
  try {
    mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    // Mic permission denied — fall back to tab-only audio
    console.warn("[web-recorder] Mic capture failed, recording tab audio only:", e);
  }

  if (!mic) {
    // Tab audio only — return the display stream's audio tracks
    return new MediaStream(tabAudioTracks);
  }

  // Mix tab + mic into one stream via Web Audio
  const audioContext = new AudioContext({ sampleRate: 48_000 });
  const tabSource = audioContext.createMediaStreamSource(new MediaStream(tabAudioTracks));
  const micSource = audioContext.createMediaStreamSource(mic);
  const destination = audioContext.createMediaStreamDestination();
  tabSource.connect(destination);
  micSource.connect(destination);

  // Attach tracker so caller can stop everything cleanly.
  // MediaStream is host-defined; we hang a private cleanup callback
  // on it via a typed intersection rather than `any`.
  const mixedStream = destination.stream as MediaStream & {
    __memoirCleanup?: () => void;
  };
  mixedStream.__memoirCleanup = () => {
    tabAudioTracks.forEach((t) => t.stop());
    mic!.getTracks().forEach((t) => t.stop());
    void audioContext.close();
  };
  return mixedStream;
}

/**
 * Mic-only capture — no tab audio, no getDisplayMedia picker.
 *
 * Used when the user is on /record without the extension installed (and
 * picks "Mic only" in the modal), or when the extension is installed but
 * no meeting tab is open. Only the user's own voice is captured.
 *
 * Pass an explicit deviceId from the in-app mic picker, or omit to use
 * the system default (Chrome will prompt with its device picker on the
 * first call for the origin).
 */
export async function requestMicOnlyCapture(
  deviceId?: string,
): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new CaptureError("not_supported", "Browser does not support microphone capture");
  }
  const audioConstraints: MediaTrackConstraints = {
    echoCancellation: true,
    noiseSuppression: true,
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
  };
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
  } catch (e) {
    const msg = (e as Error).message || String(e);
    if (msg.toLowerCase().includes("denied") || msg.toLowerCase().includes("permission")) {
      throw new CaptureError("permission_denied", msg);
    }
    throw new CaptureError("user_cancelled", msg);
  }
}

/** Stop all underlying tracks + close the AudioContext. */
export function stopMixedCapture(stream: MediaStream): void {
  try {
    stream.getTracks().forEach((t) => t.stop());
    const cleanup = (stream as MediaStream & { __memoirCleanup?: () => void }).__memoirCleanup;
    if (typeof cleanup === "function") cleanup();
  } catch {
    /* ignore */
  }
}
