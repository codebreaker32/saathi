import type { NextConfig } from "next";

/* Exported as static files and served by the Python process, so the page, the
   SSE stream and the audio all share one origin. Two origins would mean CORS
   on the stream and a mixed-content block the moment TLS is added. */
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
