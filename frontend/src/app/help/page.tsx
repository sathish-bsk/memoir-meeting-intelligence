import { Metadata } from "next";

export const metadata: Metadata = {
  title: "Help Center — Memoir",
};

export default function HelpPage() {
  return (
    <div className="min-h-screen bg-[#F8F7F4] py-16 px-4">
      <div className="max-w-3xl mx-auto bg-white rounded-xl border border-zinc-200 p-8 md:p-12">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 bg-[#5e17eb] rounded-lg flex items-center justify-center">
            <span className="text-white font-bold text-lg">M</span>
          </div>
          <h1 className="text-3xl font-bold text-zinc-900">Help Center</h1>
        </div>

        <div className="space-y-8">
          <Section title="Meeting Summary Emails">
            <p>
              After every meeting is transcribed, Memoir sends a summary email to the recorder with key decisions,
              action items, and discussion highlights. Internal team members receive a lean recap with their
              personal action items highlighted.
            </p>
            <h4 className="font-semibold text-zinc-800 mt-3">Managing email preferences</h4>
            <p>
              You can control which emails you receive from your{" "}
              <a href="/settings/notifications" className="text-indigo-600 hover:underline">
                Notification Settings
              </a>{" "}
              page. Toggle individual notification types on or off.
            </p>
          </Section>

          <Section title="Recording Meetings">
            <h4 className="font-semibold text-zinc-800 mt-3">Chrome Extension</h4>
            <p>
              Install the Memoir Chrome Extension to record meetings directly from Google Meet, Zoom, or
              Microsoft Teams in your browser. Click the Memoir icon during a meeting to start recording.
            </p>
            <h4 className="font-semibold text-zinc-800 mt-3">File Upload</h4>
            <p>
              Upload audio or video files directly from the Memoir dashboard. Supported formats include
              MP3, WAV, M4A, MP4, WebM, and more.
            </p>
          </Section>

          <Section title="AI Analysis">
            <p>
              Memoir uses AI to extract key information from your meetings:
            </p>
            <ul className="list-disc pl-5 space-y-1 text-zinc-600">
              <li><strong>Decisions Made</strong> — agreements reached during the meeting</li>
              <li><strong>Action Items</strong> — tasks with owners and urgency levels</li>
              <li><strong>Open Questions</strong> — unresolved items that need follow-up</li>
              <li><strong>Discussion Threads</strong> — key topics discussed, ordered by priority</li>
            </ul>
          </Section>

          <Section title="Sharing with External Participants">
            <p>
              When your meeting includes external participants (people outside your organization),
              you can choose to share a summary with them after the meeting. This requires explicit
              approval from the recorder — Memoir will never share meeting data with external
              participants without your consent.
            </p>
          </Section>

          <Section title="Contact Support">
            <p>
              Need help with something not covered here? Reach out to us at{" "}
              <a href="mailto:support@meetmemoir.com" className="text-indigo-600 hover:underline">
                support@meetmemoir.com
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
    <div>
      <h2 className="text-xl font-semibold text-zinc-900 mb-3">{title}</h2>
      <div className="text-sm text-zinc-600 leading-relaxed space-y-2">{children}</div>
    </div>
  );
}
