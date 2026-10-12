import { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy — Memoir",
};

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-[#F8F7F4] py-16 px-4">
      <div className="max-w-3xl mx-auto bg-white rounded-xl border border-zinc-200 p-8 md:p-12">
        <h1 className="text-3xl font-bold text-zinc-900 mb-2">Privacy Policy</h1>
        <p className="text-sm text-zinc-400 mb-8">Last updated: March 6, 2026</p>

        <div className="prose prose-zinc prose-sm max-w-none space-y-6">
          <Section title="1. Introduction">
            <p>
              Memoir (&quot;we&quot;, &quot;us&quot;, &quot;our&quot;) operates the Memoir web application at{" "}
              <a href="https://app.meetmemoir.com" className="text-indigo-600 hover:underline">app.meetmemoir.com</a>{" "}
              and the Memoir Chrome Extension. This Privacy Policy explains how we collect, use, and protect
              your information when you use our services.
            </p>
          </Section>

          <Section title="2. Information We Collect">
            <h4 className="font-semibold text-zinc-800 mt-3">Account Information</h4>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Email address and name (provided during signup)</li>
              <li>Authentication credentials (passwords are hashed, never stored in plain text)</li>
              <li>Team membership and role information</li>
            </ul>

            <h4 className="font-semibold text-zinc-800 mt-3">Meeting Data</h4>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Meeting audio recordings (captured via Chrome Extension with your explicit action)</li>
              <li>Meeting transcriptions generated from recordings</li>
              <li>AI-generated meeting summaries, action items, and intelligence</li>
              <li>Meeting metadata (title, date, duration, platform, participants)</li>
              <li>Meeting notes you create</li>
            </ul>

            <h4 className="font-semibold text-zinc-800 mt-3">Integration Data</h4>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Calendar events (when you connect Google Calendar or Outlook)</li>
              <li>CRM data (when you connect HubSpot or Zoho)</li>
              <li>Slack workspace information (when you enable Slack notifications)</li>
            </ul>

            <h4 className="font-semibold text-zinc-800 mt-3">Chrome Extension Data</h4>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Authentication tokens (stored locally in Chrome storage)</li>
              <li>Active meeting state (stored locally, not transmitted)</li>
              <li>Tab audio (captured only when you click &quot;Record&quot;, uploaded to your Memoir account)</li>
            </ul>
          </Section>

          <Section title="3. How We Use Your Information">
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Provide meeting recording, transcription, and AI analysis services</li>
              <li>Authenticate you and maintain your session</li>
              <li>Send notifications you have opted into (email, Slack, in-app)</li>
              <li>Generate team-level intelligence and analytics</li>
              <li>Improve our services and fix technical issues</li>
            </ul>
          </Section>

          <Section title="4. Data Processing & AI">
            <p>
              Meeting recordings are processed using speech-to-text services to generate transcriptions.
              Transcriptions are analyzed by AI language models to produce summaries, action items,
              and business intelligence. AI processing is performed on our servers and through
              third-party AI providers (Anthropic Claude, Groq). Your data is not used to train
              third-party AI models.
            </p>
          </Section>

          <Section title="5. Data Storage & Security">
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Data is stored on encrypted servers hosted on AWS (Mumbai, India region)</li>
              <li>Database connections use SSL/TLS encryption</li>
              <li>Passwords are hashed using industry-standard algorithms</li>
              <li>OAuth tokens are encrypted at rest using Fernet symmetric encryption</li>
              <li>API communication is encrypted via HTTPS (TLS 1.3)</li>
              <li>JWT tokens are signed using ES256 (ECDSA) keys</li>
            </ul>
          </Section>

          <Section title="6. Data Sharing">
            <p>We do not sell your personal information. We share data only in these cases:</p>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li><strong>With your team:</strong> Meeting data is shared with members of your Memoir team based on role permissions</li>
              <li><strong>Third-party services:</strong> When you explicitly connect integrations (Google, Microsoft, Zoom, Slack, HubSpot, Zoho)</li>
              <li><strong>AI providers:</strong> Meeting transcriptions are sent to AI providers for analysis (not used for model training)</li>
              <li><strong>Legal requirements:</strong> If required by law or to protect our legal rights</li>
            </ul>
          </Section>

          <Section title="7. Chrome Extension Permissions">
            <p>The Memoir Chrome Extension requests the following permissions:</p>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li><strong>storage:</strong> Store authentication tokens and meeting state locally</li>
              <li><strong>tabs:</strong> Detect active meeting tabs on Google Meet, Zoom, and Teams</li>
              <li><strong>tabCapture:</strong> Capture tab audio when you initiate a recording</li>
              <li><strong>offscreen:</strong> Process audio recording in the background</li>
              <li><strong>alarms:</strong> Schedule token refresh and meeting status checks</li>
              <li><strong>scripting:</strong> Inject the recording UI into meeting pages</li>
            </ul>
            <p className="mt-2">
              The extension only records audio when you explicitly click the &quot;Record&quot; button.
              No audio is captured passively or without your action. All code runs locally within
              the extension — no remote code is loaded or executed.
            </p>
          </Section>

          <Section title="8. Data Retention">
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Meeting data is retained as long as your account is active</li>
              <li>You can delete individual meetings and their associated data at any time</li>
              <li>Account deletion removes all your personal data within 30 days</li>
              <li>Automated database backups are retained for up to 7 days</li>
            </ul>
          </Section>

          <Section title="9. Your Rights">
            <p>You have the right to:</p>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li>Access your personal data</li>
              <li>Delete your data and account</li>
              <li>Export your meeting data</li>
              <li>Opt out of email notifications</li>
              <li>Disconnect third-party integrations at any time</li>
            </ul>
          </Section>

          <Section title="10. Contact Us">
            <p>
              If you have questions about this Privacy Policy or your data, contact us at{" "}
              <a href="mailto:hello@meetmemoir.com" className="text-indigo-600 hover:underline">
                hello@meetmemoir.com
              </a>
            </p>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-lg font-semibold text-zinc-900 mb-2">{title}</h3>
      <div className="text-sm text-zinc-600 leading-relaxed">{children}</div>
    </section>
  );
}
