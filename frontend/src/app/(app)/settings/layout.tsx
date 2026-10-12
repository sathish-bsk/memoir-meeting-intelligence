"use client";

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="max-w-4xl mx-auto">
      {children}
    </div>
  );
}
