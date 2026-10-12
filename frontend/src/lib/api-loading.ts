// Global API request loading state — used by GlobalLoadingBar component

type Listener = (loading: boolean) => void;

let activeRequests = 0;
const listeners = new Set<Listener>();

export function apiLoadingSubscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function notify() {
  const loading = activeRequests > 0;
  listeners.forEach((fn) => fn(loading));
}

export function apiRequestStart() {
  activeRequests++;
  notify();
}

export function apiRequestEnd() {
  activeRequests = Math.max(0, activeRequests - 1);
  notify();
}
