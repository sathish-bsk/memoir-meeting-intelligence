export interface SetupStep {
  key: string;
  label: string;
  done: boolean;
  comingSoon?: boolean;
  href?: string;
}

const CHROME_STORE_URL = "https://chromewebstore.google.com/detail/hbjhbdfiagiodfdpclbipljkoocmemea?utm_source=item-share-cb";

export function useSetupSteps(
  calendarConnected: boolean,
  crmConnected: boolean,
  extensionInstalled: boolean = false,
  slackConnected: boolean = false,
): SetupStep[] {
  return [
    { key: "signup", label: "Sign up complete", done: true },
    {
      key: "extension",
      label: "Install Chrome Extension",
      done: extensionInstalled,
      href: extensionInstalled ? undefined : CHROME_STORE_URL,
    },
    { key: "calendar", label: "Connect Calendar", done: calendarConnected, href: "/integrations" },
    { key: "crm", label: "Connect CRM", done: crmConnected, href: "/integrations" },
    { key: "slack", label: "Connect Slack", done: slackConnected, href: "/integrations" },
  ];
}
