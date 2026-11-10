import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pages read web/data/*.json at request time, so the folder has to ship with the server functions.
  outputFileTracingIncludes: { "/**": ["./data/**/*"] },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
