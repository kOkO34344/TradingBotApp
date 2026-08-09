"use client";

/**
 * /ledger — the record.
 *
 * Every order attempt and fill, and every backtest this project has run. They
 * share a screen because they are the same thing at two time scales: what the
 * system did, and what it has been able to show. Rule 6 — if it isn't in the
 * journal, it didn't happen — and rule 4 — negative results get reported, not
 * massaged — are the two halves of one claim to be evidence-led.
 *
 * The journal shows its own corrections. Superseded and disputed rows stay
 * visible, struck through and labelled; deleting them would destroy the audit
 * trail, and the contradiction is the information.
 */

import { UrlTabs } from "@/components/url-tabs";
import { BacktestsScreen } from "@/components/screens/backtests-screen";
import { JournalScreen } from "@/components/screens/journal-screen";

export default function LedgerPage() {
  return (
    <UrlTabs
      tabs={[
        { value: "journal", label: "Journal", content: <JournalScreen /> },
        { value: "backtests", label: "Backtests", content: <BacktestsScreen /> },
      ]}
    />
  );
}
