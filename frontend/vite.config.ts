import basicSsl from "@vitejs/plugin-basic-ssl";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `make dev-lan`: serve over HTTPS on the local network so a phone on the same Wi-Fi can open it.
// Cognito only allows plain-HTTP redirects for localhost, hence the (self-signed) certificate.
const lan = process.env.LAN === "1";

export default defineConfig({
  plugins: lan ? [react(), basicSsl()] : [react()],
  server: {
    port: 5173,
    // Cognito only redirects to the exact registered URL, so never silently pick another port.
    strictPort: true,
    host: lan ? true : undefined,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
