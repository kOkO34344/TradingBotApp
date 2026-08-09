import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The dev overlay badge sits bottom-left, exactly where the chart's data-
  // source strip lives. This app is only ever run in dev on localhost, so
  // the badge would be permanently in the way.
  devIndicators: false,

  // The eight screens became four on 2026-08-09. These keep every old URL
  // working — bookmarks, the browser's history, and any link written into a
  // commit message or the vault. Permanent, because the old routes are not
  // coming back: they are sections now, not pages.
  async redirects() {
    return [
      { source: "/ftmo", destination: "/watch", permanent: true },
      { source: "/dashboard", destination: "/watch", permanent: true },
      { source: "/positions", destination: "/watch", permanent: true },
      // The six that became tabs carry `?tab=`, so an old bookmark lands on
      // the screen it used to be rather than on whichever tab sorts first.
      { source: "/kronos", destination: "/signal?tab=forecast", permanent: true },
      { source: "/rebalance", destination: "/signal?tab=rotation", permanent: true },
      { source: "/charts", destination: "/market", permanent: true },
      { source: "/journal", destination: "/ledger?tab=journal", permanent: true },
      { source: "/backtests", destination: "/ledger?tab=backtests", permanent: true },
    ];
  },
};

export default nextConfig;
