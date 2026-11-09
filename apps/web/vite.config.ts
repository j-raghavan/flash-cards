/// <reference types="vitest/config" />
// SPDX-License-Identifier: AGPL-3.0-only
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// The API is reached under /api by default (same origin). In development the
// proxy strips that prefix and forwards to the FastAPI server, whose routes
// live at the root.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.FLASHCARDS_API_PROXY_TARGET || "http://localhost:8000";

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: "autoUpdate",
        injectRegister: false,
        includeAssets: ["favicon.svg", "icons/apple-touch-icon.png"],
        manifest: {
          name: "Flash-Cards",
          short_name: "Flash-Cards",
          description: "Spaced-repetition study with AI-assisted card creation.",
          start_url: "/",
          scope: "/",
          display: "standalone",
          background_color: "#fbfaf8",
          theme_color: "#fbfaf8",
          icons: [
            { src: "icons/icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "icons/icon-512.png", sizes: "512x512", type: "image/png" },
            {
              src: "icons/icon-maskable-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "maskable",
            },
          ],
        },
        workbox: {
          // The app shell is precached; client-side routes fall back to it.
          globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
          navigateFallback: "/index.html",
          navigateFallbackDenylist: [/^\/api\//],
          cleanupOutdatedCaches: true,
          runtimeCaching: [
            {
              // The deck list, due queues and card contents, so a study session can
              // start without a connection. Network first: online data always wins.
              // The cache is cleared on sign-out (see src/offline/apiCache.ts).
              urlPattern: ({ url, request }) =>
                request.method === "GET" && /\/api\/(decks|decks\/[^/]+\/review|cards\/[^/]+)$/.test(url.pathname),
              handler: "NetworkFirst",
              options: {
                cacheName: "flashcards-study-data",
                networkTimeoutSeconds: 4,
                expiration: { maxEntries: 2000, maxAgeSeconds: 7 * 24 * 60 * 60 },
                cacheableResponse: { statuses: [200] },
              },
            },
          ],
        },
      }),
    ],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path: string) => path.replace(/^\/api/, ""),
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      restoreMocks: true,
      unstubGlobals: true,
      coverage: {
        provider: "v8",
        include: ["src/**/*.{ts,tsx}"],
        // main.tsx only mounts the app and registers the service worker; test
        // helpers and type declarations contain no product logic.
        exclude: ["src/main.tsx", "src/test/**", "src/**/*.test.{ts,tsx}", "src/**/*.d.ts"],
        reporter: ["text", "json-summary"],
        thresholds: { lines: 97, branches: 97, functions: 97, statements: 97 },
      },
    },
  };
});
