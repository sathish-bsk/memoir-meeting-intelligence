import type { ExtensionMessage, ExtensionResponse } from "./types";

/**
 * Send a message to the background service worker and await a typed response.
 */
export function sendMessage<T = unknown>(
  message: ExtensionMessage
): Promise<ExtensionResponse<T>> {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response: ExtensionResponse<T>) => {
      if (chrome.runtime.lastError) {
        resolve({ success: false, error: chrome.runtime.lastError.message });
        return;
      }
      resolve(response ?? { success: false, error: "No response" });
    });
  });
}
