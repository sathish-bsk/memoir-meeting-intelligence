/**
 * Caption scraper — observes caption DOM for speaker changes,
 * sends ACTIVE_SPEAKER_EVENT to service worker.
 */

export type CaptionHandler = (speakerName: string) => void;

export class CaptionScraper {
  private currentSpeaker: string | null = null;
  private recordingStartTime = 0;
  private speakerStartTime = 0;
  private handler: CaptionHandler;
  /** Accumulated set of every speaker we've seen captions from in this
   *  session. The popup's participant scraper unions this with a one-shot
   *  DOM read so participants who've already spoken but aren't on the
   *  visible tile grid (or panel) still appear. */
  private seenSpeakers = new Set<string>();

  constructor(handler: CaptionHandler) {
    this.handler = handler;
  }

  /** Names accumulated from caption observations during the current session. */
  getSeenSpeakers(): string[] {
    return Array.from(this.seenSpeakers);
  }

  onCaption(speakerName: string): void {
    if (!speakerName || speakerName === this.currentSpeaker) return;

    this.seenSpeakers.add(speakerName);
    const now = Date.now();

    if (this.currentSpeaker !== null) {
      // Send end event for previous speaker
      chrome.runtime.sendMessage({
        type: "ACTIVE_SPEAKER_EVENT",
        speaker: this.currentSpeaker,
        start: (this.speakerStartTime - this.recordingStartTime) / 1000,
        end: (now - this.recordingStartTime) / 1000,
      });
    }

    this.currentSpeaker = speakerName;
    this.speakerStartTime = now;
    this.handler(speakerName);
  }

  start(recordingStartTime: number): void {
    this.recordingStartTime = recordingStartTime;
    this.currentSpeaker = null;
    this.speakerStartTime = recordingStartTime;
    // Intentionally NOT clearing seenSpeakers — we want the popup to
    // see continuity if the user starts/stops captures within the same
    // tab session.
  }

  stop(): void {
    if (this.currentSpeaker !== null) {
      const now = Date.now();
      chrome.runtime.sendMessage({
        type: "ACTIVE_SPEAKER_EVENT",
        speaker: this.currentSpeaker,
        start: (this.speakerStartTime - this.recordingStartTime) / 1000,
        end: (now - this.recordingStartTime) / 1000,
      });
      this.currentSpeaker = null;
    }
  }
}
