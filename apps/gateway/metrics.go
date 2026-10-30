package main

// Minimal Prometheus text exposition (stdlib only) for the gateway (PLAN P8 observability):
//
//	oa_gateway_requests_total{kind,status}          kind = sandbox|static|waking|auth_required|notfound|app_api|preview_auth|bridge|error
//	oa_gateway_request_duration_seconds{kind}       histogram, includes upstream time
//	oa_gateway_resolve_duration_seconds{result}     histogram of api /internal/resolve calls (result = ok|error)
//	oa_gateway_resolve_cache_total{result}          hit|miss
//
// Served at /__oa/metrics (optionally protected by OA_METRICS_TOKEN as a bearer token).

import (
	"bufio"
	"errors"
	"fmt"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

var defaultBuckets = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30}

type histogram struct {
	buckets []float64
	counts  []uint64
	sum     float64
	count   uint64
}

func (h *histogram) observe(v float64) {
	for i, b := range h.buckets {
		if v <= b {
			h.counts[i]++
		}
	}
	h.sum += v
	h.count++
}

type metrics struct {
	mu         sync.Mutex
	counters   map[string]map[string]uint64 // name → labels → value
	histograms map[string]map[string]*histogram
	help       map[string]string
}

func newMetrics() *metrics {
	return &metrics{
		counters:   map[string]map[string]uint64{},
		histograms: map[string]map[string]*histogram{},
		help: map[string]string{
			"oa_gateway_requests_total":           "Gateway requests by routing kind and status",
			"oa_gateway_request_duration_seconds": "Gateway request duration including upstream",
			"oa_gateway_resolve_duration_seconds": "Latency of api /internal/resolve calls",
			"oa_gateway_resolve_cache_total":      "Resolve cache lookups",
		},
	}
}

func labels(kv ...string) string {
	parts := make([]string, 0, len(kv)/2)
	for i := 0; i+1 < len(kv); i += 2 {
		parts = append(parts, fmt.Sprintf(`%s=%q`, kv[i], kv[i+1]))
	}
	return strings.Join(parts, ",")
}

func (m *metrics) inc(name, lbl string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.counters[name] == nil {
		m.counters[name] = map[string]uint64{}
	}
	m.counters[name][lbl]++
}

func (m *metrics) observe(name, lbl string, v float64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.histograms[name] == nil {
		m.histograms[name] = map[string]*histogram{}
	}
	h := m.histograms[name][lbl]
	if h == nil {
		h = &histogram{buckets: defaultBuckets, counts: make([]uint64, len(defaultBuckets))}
		m.histograms[name][lbl] = h
	}
	h.observe(v)
}

func (m *metrics) write(w http.ResponseWriter) {
	m.mu.Lock()
	defer m.mu.Unlock()
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	var b strings.Builder
	for _, name := range sortedKeys(m.counters) {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s counter\n", name, m.help[name], name)
		for _, l := range sortedKeys(m.counters[name]) {
			fmt.Fprintf(&b, "%s{%s} %d\n", name, l, m.counters[name][l])
		}
	}
	for _, name := range sortedKeys(m.histograms) {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s histogram\n", name, m.help[name], name)
		for _, l := range sortedKeys(m.histograms[name]) {
			h := m.histograms[name][l]
			sep := ""
			if l != "" {
				sep = ","
			}
			for i, bound := range h.buckets {
				fmt.Fprintf(&b, "%s_bucket{%s%sle=\"%g\"} %d\n", name, l, sep, bound, h.counts[i])
			}
			fmt.Fprintf(&b, "%s_bucket{%s%sle=\"+Inf\"} %d\n", name, l, sep, h.count)
			fmt.Fprintf(&b, "%s_sum{%s} %g\n%s_count{%s} %d\n", name, l, h.sum, name, l, h.count)
		}
	}
	_, _ = w.Write([]byte(b.String()))
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// recorder captures status and lets handlers tag the routing kind; it keeps Hijacker/Flusher working
// so the reverse proxy can still upgrade WebSockets (Vite HMR) and stream responses.
type recorder struct {
	http.ResponseWriter
	status int
	kind   string
}

func (r *recorder) WriteHeader(code int) {
	if r.status == 0 {
		r.status = code
	}
	r.ResponseWriter.WriteHeader(code)
}

func (r *recorder) Write(b []byte) (int, error) {
	if r.status == 0 {
		r.status = http.StatusOK
	}
	return r.ResponseWriter.Write(b)
}

func (r *recorder) Flush() {
	if f, ok := r.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

func (r *recorder) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if h, ok := r.ResponseWriter.(http.Hijacker); ok {
		if r.status == 0 {
			r.status = http.StatusSwitchingProtocols
		}
		return h.Hijack()
	}
	return nil, nil, errors.New("hijack not supported")
}

func (r *recorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

// setKind tags the request's routing kind for metrics (no-op if w isn't instrumented).
func setKind(w http.ResponseWriter, kind string) {
	if r, ok := w.(*recorder); ok {
		r.kind = kind
	}
}

func (g *Gateway) instrument(w http.ResponseWriter, r *http.Request, next func(http.ResponseWriter, *http.Request)) {
	start := time.Now()
	rec := &recorder{ResponseWriter: w, kind: "other"}
	next(rec, r)
	status := rec.status
	if status == 0 {
		status = http.StatusOK
	}
	g.metrics.inc("oa_gateway_requests_total", labels("kind", rec.kind, "status", fmt.Sprint(status)))
	g.metrics.observe("oa_gateway_request_duration_seconds", labels("kind", rec.kind), time.Since(start).Seconds())
}

func (g *Gateway) serveMetrics(w http.ResponseWriter, r *http.Request) {
	if tok := g.cfg.MetricsToken; tok != "" && r.Header.Get("Authorization") != "Bearer "+tok {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	g.metrics.write(w)
}
