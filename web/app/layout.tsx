import type { Metadata } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { Toaster } from "@/components/ui/sonner";

// Swiss instrument-panel typography. Inter Tight for everything structural —
// its tighter default tracking reads as a precision instrument rather than as
// a website — and JetBrains Mono for prices and volumes.
//
// Numbers are the entire product here, so both faces run with TABULAR figures
// (see globals.css). Proportional digits let a column of prices shift
// horizontally as the last digit ticks, which turns a quietly updating quote
// into visual noise and makes two prices genuinely hard to compare by eye.
const interTight = Inter_Tight({
  variable: "--font-inter-tight",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "TradingBotApp",
  description: "Local control panel for the FTMO and IBKR trading venues",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // `dark` is set here rather than by a system-preference script: dark is
    // the chosen default, and the theme toggle in the header flips this class.
    <html
      lang="en"
      className={`dark ${interTight.variable} ${jetbrainsMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <AppShell>{children}</AppShell>
        <Toaster richColors position="bottom-right" />
      </body>
    </html>
  );
}
