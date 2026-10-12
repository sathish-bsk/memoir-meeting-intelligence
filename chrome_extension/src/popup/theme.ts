/**
 * Revamp v2 design tokens for the popup.
 * Mirrors frontend/tailwind.config.ts brand/ink/surface palettes so the
 * popup feels visually consistent with the web app even though it can't
 * share Tailwind config (webpack pipeline is separate).
 */

export const T = {
  brand: "#5C5CE0",
  brandLight: "#EDEDFC",
  brandDark: "#4848B8",
  orange: "#E8683A",
  orangeLight: "#FFF3EE",
  green: "#00B87C",
  greenLight: "#E6F9F1",
  amber: "#F5A623",
  amberLight: "#FFF8E8",
  red: "#E5484D",
  redLight: "#FEECEE",
  ink: "#1B1B1F",
  inkSecondary: "#7C7C8A",
  inkLight: "#A0A0AE",
  surface: "#FFFFFF",
  surfaceSoft: "#F8F8FC",
  border: "#EBEBF0",
} as const;

export const FONT_STACK =
  "'Manrope', 'SF Pro Display', -apple-system, system-ui, sans-serif";

/**
 * Animations used across recording-state UIs. Inject once at popup mount
 * via <style>{POPUP_KEYFRAMES}</style> at the top of App.tsx; can't @apply
 * Tailwind here, can't use the frontend's globals.css.
 */
export const POPUP_KEYFRAMES = `
@keyframes pulse-ring {
  0%, 100% { transform: scale(0.9); opacity: 1; }
  50% { transform: scale(1.15); opacity: 0.4; }
}
@keyframes fade-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes timer-tick {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}
`;
