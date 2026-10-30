// gateway serves preview and published app domains (docs/CONTRACT.md §6):
// it asks the api how to route each host, reverse-proxies to sandboxes or serves
// static deploys, injects bridge.js into HTML, and proxies app `/api/*` calls to
// the platform's Cloud BaaS.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	cfg := Config{
		Listen:        envOr("GATEWAY_LISTEN", ":8080"),
		APIURL:        envOr("OA_API_URL", "http://127.0.0.1:8100"),
		InternalToken: envOr("OA_INTERNAL_TOKEN", "dev-internal-token"),
		PublicWebURL:  envOr("OA_PUBLIC_WEB_URL", "http://localhost:3000"),
		AppDomain:     envOr("OA_APP_DOMAIN", "app.localhost:8080"),
		MetricsToken:  os.Getenv("OA_METRICS_TOKEN"),
	}
	gw, err := New(cfg)
	if err != nil {
		log.Fatal(err)
	}
	srv := &http.Server{Addr: cfg.Listen, Handler: gw, ReadHeaderTimeout: 15 * time.Second}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(sctx)
	}()
	log.Printf("gateway listening on %s (api=%s, domain=%s)", cfg.Listen, cfg.APIURL, gw.domain)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
