import type { Metadata } from "next";
import { Plus_Jakarta_Sans, DM_Sans, JetBrains_Mono, Manrope, DM_Serif_Display } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "@/providers/query-provider";
import { GlobalLoadingBar } from "@/components/global-loading-bar";

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-jakarta",
  display: "swap",
});

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm",
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

// Revamp v2: primary sans for new pages
const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
  display: "swap",
});

// Revamp v2: display serif used for the Memoir logo glyph
const dmSerifDisplay = DM_Serif_Display({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Memoir — AI Sales Intelligence",
  description: "Record, transcribe, and extract CRM-ready intelligence from every sales call.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${jakarta.variable} ${dmSans.variable} ${jetbrains.variable} ${manrope.variable} ${dmSerifDisplay.variable} font-body antialiased`}>
        <GlobalLoadingBar />
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
