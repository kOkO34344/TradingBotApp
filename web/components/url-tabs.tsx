"use client";

/**
 * url-tabs.tsx — tabs whose selection lives in the URL.
 *
 * Eight screens became four on 2026-08-09, and six of them are now tabs. That
 * only works if a tab is addressable: `/backtests` has to keep landing on the
 * backtests table, not on the journal that happens to be first. The redirects
 * in next.config.ts point at `?tab=` for exactly that reason.
 *
 * It also buys something the old routes had for free and tabs usually lose —
 * a refresh keeps you where you were, and a tab can be linked to from a commit
 * message or the vault.
 *
 * `replace`, not `push`: flipping between two tabs is not navigation, and
 * stacking twelve history entries would make the back button useless for
 * leaving the screen.
 *
 * An unrecognised `?tab=` value falls back to the first tab rather than
 * rendering nothing. A URL someone mistyped should show the screen, not a
 * blank panel that looks like a broken app.
 */

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export interface UrlTab {
  value: string;
  label: string;
  content: React.ReactNode;
}

export function UrlTabs({ tabs }: { tabs: UrlTab[] }) {
  return (
    // useSearchParams needs a Suspense boundary; without one Next opts the
    // whole route out of static rendering and says so at build time.
    <Suspense fallback={<TabStrip tabs={tabs} active={tabs[0].value} />}>
      <UrlTabsInner tabs={tabs} />
    </Suspense>
  );
}

function UrlTabsInner({ tabs }: { tabs: UrlTab[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const requested = params.get("tab");
  const active = tabs.some((t) => t.value === requested)
    ? (requested as string)
    : tabs[0].value;

  return (
    <Tabs
      value={active}
      onValueChange={(value) => {
        const next = new URLSearchParams(params.toString());
        next.set("tab", String(value));
        router.replace(`${pathname}?${next.toString()}`, { scroll: false });
      }}
      className="gap-0"
    >
      <TabStripInner tabs={tabs} />
      {tabs.map((t) => (
        <TabsContent key={t.value} value={t.value}>
          {t.content}
        </TabsContent>
      ))}
    </Tabs>
  );
}

/** The strip alone, for the Suspense fallback — so the frame never jumps. */
function TabStrip({ tabs, active }: { tabs: UrlTab[]; active: string }) {
  return (
    <Tabs value={active} className="gap-0">
      <TabStripInner tabs={tabs} />
    </Tabs>
  );
}

function TabStripInner({ tabs }: { tabs: UrlTab[] }) {
  return (
    <TabsList
      variant="line"
      className="w-full justify-start gap-4 border-b hairline border-border px-4"
    >
      {tabs.map((t) => (
        <TabsTrigger key={t.value} value={t.value} className="silkscreen flex-none">
          {t.label}
        </TabsTrigger>
      ))}
    </TabsList>
  );
}
