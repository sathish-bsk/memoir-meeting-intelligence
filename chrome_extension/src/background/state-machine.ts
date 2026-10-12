import type { MeetingState, MeetingStateValue } from "../shared/types";
import { STORAGE_MEETING_KEY } from "../shared/constants";

/** Initial idle state */
export const IDLE_STATE: MeetingState = { state: "idle" };

/** Valid state transitions */
const VALID_TRANSITIONS: Record<MeetingStateValue, MeetingStateValue[]> = {
  idle: ["detected"],
  // detected → detected allows in-place metadata refresh (URL/title
  // change while staying in the detected state) — used by the
  // GET_STATE URL reconciliation path in service-worker.ts and by the
  // same-tab navigation branch in tabs.onUpdated. Without this entry
  // the state machine silently rejects the self-transition and the
  // popup keeps rendering the stale URL.
  detected: ["joining", "idle", "detected"],
  joining: ["starting_to_record", "recording", "completed", "detected", "idle"],
  // Intermediate state between joining and recording. Always exits via
  // a 1.5 s timer on the happy path; the centralized clear in transitionTo()
  // guarantees a stale timer cannot force "recording" after the state has
  // moved to any of the failure / completion paths listed here.
  starting_to_record: ["recording", "completed", "detected", "idle"],
  recording: ["finalizing_upload", "completed", "detected", "idle"],
  finalizing_upload: ["completed", "detected", "idle"],
  completed: ["idle", "detected"],
};

/** Check if a transition is valid */
export function canTransition(from: MeetingStateValue, to: MeetingStateValue): boolean {
  return VALID_TRANSITIONS[from]?.includes(to) ?? false;
}

/** Persist meeting state to storage */
export async function saveMeetingState(state: MeetingState): Promise<void> {
  await chrome.storage.local.set({ [STORAGE_MEETING_KEY]: state });
}

/** Load meeting state from storage */
export async function loadMeetingState(): Promise<MeetingState> {
  const result = await chrome.storage.local.get(STORAGE_MEETING_KEY);
  return result[STORAGE_MEETING_KEY] ?? IDLE_STATE;
}

/** Transition to a new state, with validation */
export function transition(
  current: MeetingState,
  to: MeetingStateValue,
  updates?: Partial<MeetingState>
): MeetingState {
  if (!canTransition(current.state, to)) {
    console.warn(`[Memoir] Invalid transition: ${current.state} → ${to}`);
    return current;
  }

  const next: MeetingState = {
    ...current,
    ...updates,
    state: to,
  };

  // Clean up fields on idle
  if (to === "idle") {
    return { state: "idle" };
  }

  return next;
}
