import type { Metadata } from "next";
import { Chakra_Petch, Spline_Sans_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { Toaster } from "@/components/ui/sonner";
import { CommandPalette } from "@/components/command-palette";

// Chakra Petch has CHAMFERED stems — every corner is cut rather than
// rounded — which is why the plates in globals.css are cut the same way. Type
// and surface saying the same thing is what keeps an assertive typeface
// reading as engineering rather than as decoration.
//
// It has no width axis, unlike the Archivo it replaced, so the wide silkscreen
// labels get their width from TRACKING alone (see `.silkscreen`). That is a
// real constraint, not an oversight: letterspacing widens the gaps without
// widening the letters, so the labels are airier and slightly quieter than
// before. Do not substitute a condensed weight to compensate — it would make
// the label compete with the number beside it.
//
// Not a variable font, so its weights are named.
const chakraPetch = Chakra_Petch({
  variable: "--font-chakra-petch",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

// Spline Sans Mono carries every number, and deliberately stays out of Chakra
// Petch's way: clean, contemporary, no chamfers of its own. A second
// characterful face here would fight the first, and the numbers are the one
// thing on screen that must never be interesting to look at — only easy to
// read. Variable weight, so no weight list is needed.
//
// Numbers are the entire product here, so both faces run with TABULAR figures
// (see globals.css). Proportional digits let a column of prices shift
// horizontally as the last digit ticks, which turns a quietly updating quote
// into visual noise and makes two prices genuinely hard to compare by eye.
const splineSansMono = Spline_Sans_Mono({
  variable: "--font-spline-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "TradingBotApp",
  description: "Watch station for the FTMO trading venue",
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
      className={`dark ${chakraPetch.variable} ${splineSansMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <AppShell>{children}</AppShell>
        <CommandPalette />
        <Toaster richColors position="bottom-right" />
      </body>
    </html>
  );
}
