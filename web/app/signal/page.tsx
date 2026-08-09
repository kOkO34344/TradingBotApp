"use client";

/**
 * /signal — where a forecast becomes a plan.
 *
 * Kronos's ranking and the FTMO plan it produces. They were separate routes;
 * they are one question asked twice, and splitting them meant you could read
 * a plan without ever seeing the sampling spread the ranking sits on. The
 * order is deliberate — forecast first — so the evidence sits upstream of the
 * plan in the layout as well as in the code.
 *
 * There is no approve/decline tab any more. The human-approved rotation
 * belonged to IBKR, which was removed on 2026-08-09; FTMO's runner trades
 * unattended, so the honest controls are "preview what it would do" here and
 * "arm/disarm" in the header — not a button that implies a decision the
 * system does not actually wait for.
 */

import { UrlTabs } from "@/components/url-tabs";
import { FtmoKronosPanel } from "@/components/ftmo-kronos-panel";
import { KronosScreen } from "@/components/screens/kronos-screen";

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
      ]}
    />
  );
}
