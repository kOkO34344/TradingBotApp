"use client";

/**
 * /market — prices and indicators, on the venue that trades.
 *
 * Reads /api/ftmo/bars, not /api/bars. The IBKR path returned a connection
 * error on every request once Gateway went down, and rule 9 retired that venue
 * — so it was never coming back. /api/bars still exists and still works when
 * Gateway is up; nothing was deleted.
 *
 * No indicator math happens here. Every overlay is computed by indicators.py,
 * so the chart, the research agent's notes and the terminal app cannot
 * disagree about what RSI(14) was.
 */

import { ChartsScreen } from "@/components/screens/charts-screen";

export default function MarketPage() {
  return <ChartsScreen />;
}
