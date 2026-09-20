import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: the whole UI is client-rendered ("use client") and talks to
  // the engine over fetch/SSE, so there is no server half to host. That is what
  // makes Amplify a fit for it -- and it is also why the PYTHON engine cannot
  // live there and needs its own compute.
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
