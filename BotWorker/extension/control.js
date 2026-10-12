// Memoir BotWorker audio capture — control page
//
// Loaded by Playwright (page.goto chrome-extension://<id>/control.html)
// so Python can drive the extension via window.startCapture / stopCapture
// while running with full chrome.runtime privileges.

// targetUrl is the meeting tab's CURRENT URL as Playwright observes it
// (bot._page.url after navigation). We DO NOT use chrome.tabs.query
// with {active:true} — opening this page made the control tab the active
// one in the same window. Instead background filters tabs by normalized
// origin+pathname to find the right Meet/Teams/Zoom tab.
window.startCapture = async ({ targetUrl, wsPort }) => {
  if (!targetUrl) throw new Error("startCapture: targetUrl required");

  const findResp = await chrome.runtime.sendMessage({
    type: "FIND_MEETING_TAB", targetUrl,
  });
  if (findResp && findResp.error) throw new Error("findMeetingTab: " + findResp.error);
  const tabId = findResp.tabId;
  console.log("[Memoir Control] target tab:", tabId, findResp.url);

  const idResp = await chrome.runtime.sendMessage({ type: "GET_STREAM_ID", tabId });
  if (idResp && idResp.error) throw new Error("getStreamId: " + idResp.error);

  // background does NOT respond until offscreen reports
  // OFFSCREEN_READY_TO_RECORD or OFFSCREEN_FAILED.
  const startResp = await chrome.runtime.sendMessage({
    type: "START_TAB_AUDIO",
    streamId: idResp.streamId,
    wsPort,
  });
  if (startResp && startResp.error) throw new Error("start: " + startResp.error);
  return { ok: true, tabId, info: startResp.info };
};

// stopCapture awaits offscreen's confirmation that capture has fully torn
// down: worklet disconnected, WebSocket buffer drained + closed, EOF marker
// sent. Background withholds sendResponse until OFFSCREEN_STOPPED arrives.
window.stopCapture = async () => {
  const resp = await chrome.runtime.sendMessage({ type: "STOP_TAB_AUDIO" });
  if (resp && resp.error) throw new Error("stop: " + resp.error);
  return resp || { ok: true };
};

window.pollStatus = async () => {
  return await chrome.runtime.sendMessage({ type: "GET_STATUS" });
};
