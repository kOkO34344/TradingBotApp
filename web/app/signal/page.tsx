"use client";

/**
 * /signal — where a forecast becomes a decision.
 *
 * Kronos's ranking, the FTMO plan it produces, and the IBKR rotation proposal
 * that still needs a human yes. They were three routes; they are one question
 * asked three ways, and splitting them meant you could approve a rebalance
 * without ever seeing the sampling spread the ranking sits on.
 *
 * The order is deliberate — forecast, then plan, then approval — so the
 * evidence sits upstream of the button in the layout as well as in the code.
 */

import { UrlTabs } from "@/components/url-tabs";
import { FtmoKronosPanel } from "@/components/ftmo-kronos-panel";
import { KronosScreen } from "@/components/screens/kronos-screen";
import { RebalanceScreen } from "@/components/screens/rebalance-screen";

export default function SignalPage() {
  return (
    <UrlTabs
      tabs={[
        { value: "forecast", label: "Forecast", content: <KronosScreen /> },
        {
          value: "plan",
          label: "FTMO plan",
          content: (
            <div className="mx-auto w-full max-w-[1480px] px-4 py-5">
              <FtmoKronosPanel />
            </div>
          ),
        },
        { value: "rotation", label: "IBKR rotation", content: <RebalanceScreen /> },
      ]}
    />
  );
}
