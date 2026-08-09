import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/app-shell";
import { Toaster } from "@/components/ui/sonner";
import { CommandPalette } from "@/components/command-palette";

// Archivo is loaded WITH ITS WIDTH AXIS, and that axis is the point: section
// headings and column headers run at ~118% width, uppercase and widely
// tracked, so they read as silkscreen labelling on equipment rather than as
// headings on a page (see `.silkscreen` in globals.css). Body text runs at
// normal width from the same family, so the panel has one voice at two
// registers instead of two competing families.
//
// IBM Plex Mono carries every number. It was drawn for machines and its
// figures are unmistakable at small sizes — a 5 cannot be misread as an S in
// a stop price at 11px, which is the size a stop price actually gets read at.
//
// Numbers are the entire product here, so both faces run with TABULAR figures
// (see globals.css). Proportional digits let a column of prices shift
// horizontally as the last digit ticks, which turns a quietly updating quote
// into visual noise and makes two prices genuinely hard to compare by eye.
const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  axes: ["wdth"],
  display: "swap",
});

// Not a variable font, so its weights have to be named. Plex Mono ships no
// width axis and needs none — it is only ever set as data.
const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
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
      className={`dark ${archivo.variable} ${plexMono.variable} h-full antialiased`}
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
