// sandboxd is the in-sandbox RPC daemon: command execution, filesystem,
// supervised processes, port probing and a persistent Python kernel.
// See docs/CONTRACT.md §5.
package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

const Version = "0.1.0"

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	listen := flag.String("listen", envOr("SANDBOXD_LISTEN", ":49999"), "listen address")
	root := flag.String("root", envOr("SANDBOXD_ROOT", "/workspace"), "workspace root")
	token := flag.String("token", envOr("SANDBOXD_TOKEN", ""), "auth token (X-Sandbox-Token)")
	flag.Parse()

	absRoot, err := filepath.Abs(*root)
	if err != nil {
		log.Fatalf("root: %v", err)
	}
	if err := os.MkdirAll(absRoot, 0o755); err != nil {
		log.Fatalf("root: %v", err)
	}
	// Resolve symlinks (macOS /var -> /private/var) so containment checks are stable.
	if r, err := filepath.EvalSymlinks(absRoot); err == nil {
		absRoot = r
	}

	srv := NewServer(absRoot, *token)
	httpSrv := &http.Server{Addr: *listen, Handler: srv, ReadHeaderTimeout: 10 * time.Second}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		srv.Shutdown()
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(sctx)
	}()

	log.Printf("sandboxd %s listening on %s root=%s", Version, *listen, absRoot)
	if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
