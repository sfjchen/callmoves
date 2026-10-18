import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prototype reads JSON from public/data at request time, so pages are plain dynamic routes.
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
