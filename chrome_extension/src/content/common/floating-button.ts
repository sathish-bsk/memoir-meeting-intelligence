import { sendMessage } from "../../shared/messaging";
import { NOTES_DEBOUNCE_MS } from "../../shared/constants";
import styles from "./styles.css";

export interface ToolbarPlacement {
  /** The leave/hangup button — used to position our button next to it */
  container: Element;
  leaveButton: Element;
}

/** Manages the Shadow DOM "Record with Memoir" button + quick notes UI.
 *  Supports two placement modes:
 *  - Toolbar: fixed-position anchored next to the Leave button
 *  - Floating: fixed position bottom-right (fallback)
 */
export class FloatingButton {
  private root: HTMLElement;
  private shadow: ShadowRoot;
  private button: HTMLButtonElement;
  private notesRoot: HTMLElement | null = null;
  private notesShadow: ShadowRoot | null = null;
  private notesPanel: HTMLDivElement | null = null;
  private notesTextarea: HTMLTextAreaElement | null = null;
  private notesVisible = false;
  private debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private positionInterval: ReturnType<typeof setInterval> | null = null;
  private state: "record" | "joining" | "starting_to_record" | "uploading" | "recording" | "stopping" | "hidden" = "hidden";
  private mode: "toolbar" | "floating";
  private anchorButton: Element | null = null;
  private dragOffsetX = 0;
  private dragOffsetY = 0;
  private onDragMove: ((e: MouseEvent) => void) | null = null;
  private onDragEnd: ((e: MouseEvent) => void) | null = null;
  // SW keep-alive port — held while in joining/recording so the service
  // worker isn't terminated mid-wait. See service-worker.ts comment on
  // KEEPALIVE_PORT_NAME for the why.
  private keepAlivePort: chrome.runtime.Port | null = null;
  private keepAliveActive = false;
  // Document-level click handler that detects when the host admits the
  // bot. Used as an INSTANT trigger for the "Starting to record..." UI
  // state — the host's click happens in the same browser as the extension,
  // so we can react in ~50 ms instead of waiting 3-5 s for the bot's
  // admission detection -> orchestrator -> core_api -> extension poll
  // chain. Bound once in the constructor, removed in destroy().
  private admitClickHandler: ((e: MouseEvent) => void) | null = null;

  constructor(placement?: ToolbarPlacement) {
    // Remove any stale FloatingButton root left behind by a prior content
    // script instance. Chrome doesn't kill old content scripts when you
    // reload the extension via chrome://extensions, so without this the
    // new injection creates a SECOND button DOM that visually stacks on
    // the old one (witnessed 2026-05-13 — user saw "Record with Memoir"
    // and "Joining..." rendered on top of each other). querySelectorAll
    // handles the case of >2 stale roots from repeated reloads.
    document.querySelectorAll("#memoir-extension-root").forEach((el) => el.remove());

    this.root = document.createElement("div");
    this.root.id = "memoir-extension-root";
    this.shadow = this.root.attachShadow({ mode: "closed" });

    // Inject styles
    const style = document.createElement("style");
    style.textContent = styles;
    this.shadow.appendChild(style);

    // Create button
    this.button = document.createElement("button");
    this.button.addEventListener("click", () => this.handleClick());
    this.shadow.appendChild(this.button);

    if (placement) {
      // Toolbar mode — fixed-position anchored to the right of the leave button
      this.mode = "toolbar";
      this.anchorButton = placement.leaveButton;
      this.button.className = "memoir-toolbar-btn memoir-toolbar-btn--record";
      this.button.textContent = "Record with Memoir";

      // Use fixed positioning anchored to the leave button
      this.root.style.position = "fixed";
      this.root.style.zIndex = "2147483647";
      document.body.appendChild(this.root);

      this.updatePosition();
      // Re-position periodically (toolbar can move on resize/scroll)
      this.positionInterval = setInterval(() => this.updatePosition(), 500);
    } else {
      // Floating mode — fixed position fallback
      this.mode = "floating";
      this.button.className = "memoir-floating-btn memoir-floating-btn--record";
      this.button.textContent = "Record with Memoir";
      document.body.appendChild(this.root);
    }

    // Document-level click listener for instant admission detection.
    // Registered with capture:true so we observe the click even if Meet's
    // own handler calls stopPropagation later. The handler short-circuits
    // when state !== "joining", so attaching at construct time is cheap.
    this.admitClickHandler = (e: MouseEvent) => this.handleAdmitClick(e);
    document.addEventListener("click", this.admitClickHandler, { capture: true });
  }

  /** Position the button to the right of the anchor (leave) button */
  private updatePosition() {
    if (!this.anchorButton) return;
    const rect = this.anchorButton.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return; // element not visible

    const centerY = rect.top + rect.height / 2;
    this.root.style.top = `${centerY}px`;
    this.root.style.left = `${rect.right + 12}px`;
    this.root.style.transform = "translateY(-50%)";
  }

  /** Document-level click observer. Detects the host clicking
   *  "Admit" / "Admit all" on the Meet/Teams/Zoom waiting-room
   *  notification and instantly flips the UI to "starting_to_record"
   *  via the SW — avoiding the 3-5 s wait for the bot's own admission
   *  detection to propagate back through the backend.
   */
  private handleAdmitClick(e: MouseEvent) {
    // Only fire while we're awaiting admission. Outside the joining
    // state, any "Admit" text in the page is unrelated UI.
    if (this.state !== "joining") return;

    // Phase 1 — walk up from the click target looking for an Admit
    // button. Meet wraps the visible label in child <span>s, so
    // e.target is rarely the <button> itself.
    let admitButton: HTMLElement | null = null;
    let el = e.target as HTMLElement | null;
    for (let i = 0; i < 5 && el; i++, el = el.parentElement) {
      if (el.tagName !== "BUTTON") continue;
      const text = (el.textContent || "").trim().toLowerCase();
      const aria = (el.getAttribute("aria-label") || "").toLowerCase();
      // Match "Admit", "Admit all", "Admit entry", "Admit guest", etc.
      // Word boundary on textContent prevents matching "admitted" (which
      // appears as a label AFTER admission). aria-label fallback catches
      // localized variants where the visible text differs.
      if (/^admit\b/.test(text) || aria.includes("admit")) {
        admitButton = el;
        break;
      }
    }
    if (!admitButton) return;

    // Phase 2 — verify the admit applies to OUR bot. Walk further up
    // from the admit button. Per-guest "Admit" buttons live inside a
    // notification card 2-4 levels above the button; "Admit all" lives
    // in a notifications bar 5-8 levels above the per-guest cards. In
    // both cases the bot's display name appears somewhere in the
    // ancestor chain. If we DON'T find the bot's name, the click was
    // for a different guest in a multi-guest waiting room — let the
    // backend poll signal handle admission of our bot when it actually
    // happens.
    //
    // Hardcoded list of bot display names to match against.
    //   "Meeting Notetaker" — BotWorker's guest-mode BOT_NAME default
    //     (BotWorker/app/core/config.py:36, orchestrator fallback at
    //     orchestrator/src/api/router.ts:58).
    //   "Support Memoir"   — display name on the hello@meetmemoir.com
    //     Google account used by Phase 3 authenticated joins. Meet
    //     shows the Google account name for signed-in users, not the
    //     "Enter your name" guest field, so the matcher needs both.
    // If an operator overrides BOT_NAME via env OR renames the Phase 3
    // Google account, click detection silently falls back to the
    // poll-based path — slower but still works.
    const BOT_NAMES = ["Meeting Notetaker", "Support Memoir"];
    let container: HTMLElement | null = admitButton.parentElement;
    for (let i = 0; i < 8 && container; i++, container = container.parentElement) {
      const txt = container.textContent || "";
      if (BOT_NAMES.some((name) => txt.includes(name))) {
        chrome.runtime.sendMessage({ type: "BOT_ADMITTED_LOCALLY" }).catch(() => {});
        return;
      }
    }
    // Bot's name not in any nearby ancestor — admit was for a
    // different guest. Don't fire; let the backend handle our bot.
  }

  private handleClick() {
    if (this.state === "record") {
      // Optimistic state flip: render "Joining..." immediately so the
      // user sees instant feedback, instead of waiting for the service
      // worker's createMeeting + startBot API round-trips (~0.5-2s)
      // before transitionTo("joining") propagates back via
      // RECORDING_STATUS_UPDATE. If dispatch fails, the service worker
      // sends a state update back to "record" / "error" and setState
      // will re-render accordingly.
      this.setState("joining");
      sendMessage({ type: "START_RECORDING_BOT" });
    } else if (this.state === "recording") {
      if (window.confirm("Stop recording?")) {
        // Optimistic transient state: render "Stopping…" immediately so
        // the user gets instant feedback. The bot still has to finalize
        // (FFmpeg flush + upload last chunks) and the webhook pile-up
        // can stretch the visible "recording" status by 10-20 s — that
        // gap looks broken if the button doesn't move. The SW will push
        // the real next state (uploading / record) when it lands and
        // setState() will overwrite this transient one.
        this.setState("stopping");
        sendMessage({ type: "STOP_RECORDING" });
      }
    }
  }

  private get btnPrefix(): string {
    return this.mode === "toolbar" ? "memoir-toolbar-btn" : "memoir-floating-btn";
  }

  setState(newState: "record" | "joining" | "starting_to_record" | "uploading" | "recording" | "stopping" | "hidden") {
    // Idempotent — repeated status updates with the same state should be a no-op
    // so we don't rebuild button.innerHTML (which would tear down the notepad
    // click handler) or stomp on the user's open notes panel.
    if (this.state === newState) return;

    // Show an error toast only when the recording NEVER actually started —
    // i.e., the button reverts from joining/starting_to_record back to
    // "record" without ever reaching the recording state. Transitions
    // from recording/uploading → record happen on normal stop (status
    // moves to "processing") and are NOT failures.
    const previousState = this.state;
    const recordingNeverStarted =
      previousState === "joining" ||
      previousState === "starting_to_record";
    if (recordingNeverStarted && newState === "record") {
      this.showRevertToast();
    }

    // Sticky "stopping" guard: once the user has confirmed the stop
    // alert, we MUST NOT flip back to "recording" just because the next
    // /status poll still shows recording (the cache cascade for
    // meeting_ended/recording_complete hasn't landed yet — that's the
    // whole reason we set "stopping" optimistically). Only progress
    // forward to uploading / record / hidden. Anything else is a stale
    // SW poll racing the bot finalization and gets ignored.
    if (previousState === "stopping" && newState === "recording") {
      return;
    }

    this.state = newState;
    const p = this.btnPrefix;

    // Hold an SW keep-alive port while waiting on backend state changes.
    // Without this, Chrome MV3 terminates the service worker after ~30 s
    // of idle, killing the 1.5 s setInterval poll — the next poll then
    // waits for the 30 s chrome.alarms backup, producing the user-visible
    // 15-20 s gap between bot admission and the "Stop Recording" flip.
    if (
      newState === "joining" ||
      newState === "starting_to_record" ||
      newState === "recording" ||
      newState === "uploading" ||
      newState === "stopping"
    ) {
      this.startKeepAlive();
    } else {
      this.stopKeepAlive();
    }

    switch (newState) {
      case "record":
        this.button.className = `${p} ${p}--record`;
        this.button.innerHTML = "Record with Memoir";
        this.button.style.display = "inline-flex";
        this.root.style.display = "";
        this.hideNotes();
        break;

      case "joining":
        this.button.className = `${p} ${p}--joining ${p}--disabled`;
        this.button.innerHTML = `<span class="memoir-spinner"></span> Joining...`;
        this.button.style.display = "inline-flex";
        this.root.style.display = "";
        break;

      case "starting_to_record":
        // Intermediate state shown for ~1.5 s after the bot is admitted
        // and before its recorder is plausibly capturing audio. Same
        // disabled-spinner look as "joining" so the visual continuity
        // tells the user "still doing background work, almost there".
        this.button.className = `${p} ${p}--joining ${p}--disabled`;
        this.button.innerHTML = `<span class="memoir-spinner"></span> Starting to record...`;
        this.button.style.display = "inline-flex";
        this.root.style.display = "";
        break;

      case "uploading":
        this.button.className = `${p} ${p}--joining ${p}--disabled`;
        this.button.innerHTML = `<span class="memoir-spinner"></span> Uploading recording...`;
        this.button.style.display = "inline-flex";
        this.root.style.display = "";
        break;

      case "stopping":
        // Transient state set the moment the user confirms the stop
        // alert. Label deliberately reads "Uploading recording..." to
        // match the SW-driven "uploading" state below — after the user
        // confirms stop, the bot's FFmpeg is genuinely flushing + the
        // last chunks are uploading. Using one consistent label across
        // stopping → uploading → record avoids the user perceiving a
        // flicker as the SW catches up to the real backend state.
        this.button.className = `${p} ${p}--joining ${p}--disabled`;
        this.button.innerHTML = `<span class="memoir-spinner"></span> Uploading recording...`;
        this.button.style.display = "inline-flex";
        this.root.style.display = "";
        break;

      case "recording":
        this.button.className = `${p} ${p}--recording`;
        this.button.innerHTML = `
          <span class="memoir-rec-dot"></span>
          Stop Recording
          <button class="memoir-notepad-btn" title="Quick notes">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
            </svg>
          </button>`;
        const notepadBtn = this.shadow.querySelector(".memoir-notepad-btn");
        notepadBtn?.addEventListener("click", (e) => {
          e.stopPropagation();
          this.toggleNotes();
        });
        this.button.style.display = "inline-flex";
        this.root.style.display = "";
        break;

      case "hidden":
        this.button.style.display = "none";
        this.root.style.display = "none";
        this.hideNotes();
        break;
    }
  }

  private toggleNotes() {
    if (this.notesVisible) {
      this.hideNotes();
    } else {
      this.showNotes();
    }
  }

  private showNotes() {
    if (this.notesPanel) {
      this.notesPanel.style.display = "block";
      this.notesVisible = true;
      return;
    }

    // Mount the notes panel in its own root attached directly to body.
    // The recording-button root has transform: translateY(-50%) which would
    // make any descendant position:fixed element anchor to the root rather
    // than the viewport — that breaks both initial placement and dragging.
    this.notesRoot = document.createElement("div");
    this.notesRoot.id = "memoir-notes-root";
    this.notesShadow = this.notesRoot.attachShadow({ mode: "closed" });
    const notesStyle = document.createElement("style");
    notesStyle.textContent = styles;
    this.notesShadow.appendChild(notesStyle);
    document.body.appendChild(this.notesRoot);

    this.notesPanel = document.createElement("div");
    this.notesPanel.className = "memoir-notes-panel";
    this.notesPanel.innerHTML = `
      <div class="memoir-notes-header">
        <span class="memoir-notes-title">Quick Notes</span>
        <button class="memoir-notes-close">&times;</button>
      </div>
      <textarea class="memoir-notes-textarea" placeholder="Jot down notes during the meeting..."></textarea>
      <div class="memoir-notes-footer">Auto-saved</div>
    `;
    this.notesShadow.appendChild(this.notesPanel);

    this.notesPanel.querySelector(".memoir-notes-close")?.addEventListener("click", () => {
      this.hideNotes();
    });

    this.notesTextarea = this.notesPanel.querySelector(".memoir-notes-textarea") as HTMLTextAreaElement;

    // Load existing notes
    sendMessage<{ content: string }>({ type: "GET_NOTES" }).then((res) => {
      if (res.success && res.data?.content && this.notesTextarea) {
        this.notesTextarea.value = res.data.content;
      }
    });

    this.notesTextarea.addEventListener("input", () => {
      if (this.debounceTimer) clearTimeout(this.debounceTimer);
      this.debounceTimer = setTimeout(() => {
        if (this.notesTextarea) {
          sendMessage({ type: "SAVE_NOTES", content: this.notesTextarea.value });
        }
      }, NOTES_DEBOUNCE_MS);
    });

    // Drag-to-reposition + persisted position
    const header = this.notesPanel.querySelector(".memoir-notes-header") as HTMLElement;
    if (header) this.attachDragHandlers(header);
    this.applySavedPosition();

    this.notesVisible = true;
  }

  /** Position the panel: saved position if any, else anchored above the
   *  recording button so it visually flows from the notes icon. */
  private async applySavedPosition() {
    if (!this.notesPanel) return;
    let pos: { top: number; left: number } | undefined;
    try {
      const saved = await chrome.storage.local.get("memoir_notes_pos");
      pos = saved?.memoir_notes_pos as { top: number; left: number } | undefined;
    } catch {
      // storage unavailable
    }
    if (!this.notesPanel) return;

    const panelRect = this.notesPanel.getBoundingClientRect();
    const w = panelRect.width || 280;
    const h = panelRect.height || 220;

    if (!pos) {
      // Anchor above the recording button: bottom-edge of panel ~8px above
      // the button's top-edge, right-edge aligned to button's right-edge.
      const btnRect = this.button.getBoundingClientRect();
      pos = {
        top: btnRect.top - h - 8,
        left: btnRect.right - w,
      };
    }

    const top = Math.max(8, Math.min(pos.top, window.innerHeight - 40));
    const left = Math.max(40 - w, Math.min(pos.left, window.innerWidth - 40));
    this.notesPanel.style.top = `${top}px`;
    this.notesPanel.style.left = `${left}px`;
    this.notesPanel.style.right = "auto";
    this.notesPanel.style.bottom = "auto";
  }

  private attachDragHandlers(header: HTMLElement) {
    header.addEventListener("mousedown", (e) => {
      // Don't start drag from the close button
      const target = e.target as HTMLElement;
      if (target.closest(".memoir-notes-close")) return;
      if (!this.notesPanel) return;

      e.preventDefault();
      const rect = this.notesPanel.getBoundingClientRect();
      this.dragOffsetX = e.clientX - rect.left;
      this.dragOffsetY = e.clientY - rect.top;
      header.classList.add("memoir-dragging");

      this.onDragMove = (ev: MouseEvent) => {
        if (!this.notesPanel) return;
        const w = this.notesPanel.offsetWidth;
        const left = Math.max(40 - w, Math.min(ev.clientX - this.dragOffsetX, window.innerWidth - 40));
        const top = Math.max(0, Math.min(ev.clientY - this.dragOffsetY, window.innerHeight - 40));
        this.notesPanel.style.top = `${top}px`;
        this.notesPanel.style.left = `${left}px`;
        this.notesPanel.style.right = "auto";
        this.notesPanel.style.bottom = "auto";
      };

      this.onDragEnd = () => {
        if (this.onDragMove) window.removeEventListener("mousemove", this.onDragMove);
        if (this.onDragEnd) window.removeEventListener("mouseup", this.onDragEnd);
        this.onDragMove = null;
        this.onDragEnd = null;
        header.classList.remove("memoir-dragging");
        if (this.notesPanel) {
          const top = parseFloat(this.notesPanel.style.top || "0");
          const left = parseFloat(this.notesPanel.style.left || "0");
          chrome.storage.local.set({ memoir_notes_pos: { top, left } }).catch(() => {});
        }
      };

      window.addEventListener("mousemove", this.onDragMove);
      window.addEventListener("mouseup", this.onDragEnd);
    });
  }

  private hideNotes() {
    if (this.notesPanel) {
      this.notesPanel.style.display = "none";
    }
    this.notesVisible = false;
  }

  getNotesContent(): string {
    return this.notesTextarea?.value ?? "";
  }

  /** Whether the button is anchored to a toolbar leave button (vs floating) */
  isAnchored(): boolean {
    return this.mode === "toolbar";
  }

  /** Get current button state */
  getState(): "record" | "joining" | "starting_to_record" | "uploading" | "recording" | "stopping" | "hidden" {
    return this.state;
  }

  /** Open a port to the service worker that keeps it alive past the
   *  ~30 s MV3 idle timeout. The port itself carries no traffic — its
   *  open state is the signal. Chrome force-disconnects ports at the
   *  5 min hard cap, so we reconnect on disconnect for arbitrary-length
   *  meetings.
   */
  private startKeepAlive() {
    if (this.keepAliveActive) return;
    this.keepAliveActive = true;
    this.connectKeepAlive();
  }

  private stopKeepAlive() {
    this.keepAliveActive = false;
    if (this.keepAlivePort) {
      try {
        this.keepAlivePort.disconnect();
      } catch {
        // already disconnected
      }
      this.keepAlivePort = null;
    }
  }

  private connectKeepAlive() {
    if (!this.keepAliveActive) return;
    try {
      this.keepAlivePort = chrome.runtime.connect({ name: "memoir-keepalive" });
    } catch {
      // Extension context invalidated (extension reloaded). Don't retry —
      // the content script will be re-injected with a fresh context.
      this.keepAliveActive = false;
      this.keepAlivePort = null;
      return;
    }
    this.keepAlivePort.onDisconnect.addListener(() => {
      this.keepAlivePort = null;
      if (this.keepAliveActive) {
        // Chrome force-closed the port (5 min cap or SW crash). Reconnect
        // immediately so the SW stays alive for the rest of this state.
        this.connectKeepAlive();
      }
    });
  }

  /** Surface a transient error toast inside the shadow root when the
   *  button is reverted from an active state back to "record" — i.e. the
   *  bot failed to record. Auto-dismisses after 5 seconds. Idempotent
   *  (re-shows if called again).
   */
  private showRevertToast() {
    try {
      const existing = this.shadow.querySelector(".memoir-revert-toast");
      if (existing) existing.remove();
      const toast = document.createElement("div");
      toast.className = "memoir-revert-toast";
      toast.textContent = "Recording didn't start. Please try again.";
      toast.style.cssText = `
        position: fixed;
        bottom: 80px;
        right: 24px;
        background: #DC2626;
        color: white;
        padding: 10px 16px;
        border-radius: 8px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 13px;
        font-weight: 500;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
        z-index: 2147483647;
        animation: memoir-toast-in 0.25s ease-out;
      `;
      this.shadow.appendChild(toast);
      setTimeout(() => {
        try {
          toast.remove();
        } catch {
          // already removed
        }
      }, 5000);
    } catch {
      // shadow root unavailable — silent no-op
    }
  }

  destroy() {
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    if (this.positionInterval) clearInterval(this.positionInterval);
    if (this.onDragMove) window.removeEventListener("mousemove", this.onDragMove);
    if (this.onDragEnd) window.removeEventListener("mouseup", this.onDragEnd);
    if (this.admitClickHandler) {
      document.removeEventListener("click", this.admitClickHandler, { capture: true });
      this.admitClickHandler = null;
    }
    this.stopKeepAlive();
    this.notesRoot?.remove();
    this.root.remove();
  }
}
