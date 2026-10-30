// Browser error reporting (PLAN P8). Enabled only when NEXT_PUBLIC_SENTRY_DSN is set at build time;
// the SDK is loaded lazily so it costs nothing otherwise.
let started = false;

export async function initSentry() {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn || started || typeof window === "undefined") return;
  started = true;
  const Sentry = await import("@sentry/browser");
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_OA_ENV || "dev",
    release: process.env.NEXT_PUBLIC_OA_RELEASE,
    tracesSampleRate: 0,
    sendDefaultPii: false,
  });
}
