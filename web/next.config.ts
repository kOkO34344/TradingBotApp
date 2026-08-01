import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The dev overlay badge sits bottom-left, exactly where the chart's data-
  // source strip lives. This app is only ever run in dev on localhost, so
  // the badge would be permanently in the way.
  devIndicators: false,
};

export default nextConfig;
