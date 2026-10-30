package main

import (
	"net/http"
	"strings"
	"testing"
)

func TestMetricsCountKindsAndResolveCache(t *testing.T) {
	api, ts, gw := setup(t)
	sbx := newSandbox(t)
	host := "5173-" + chat + "-preview.app.localhost"
	api.res[host] = Resolution{Kind: "sandbox", Upstream: sbx.URL}
	readAll(get(t, ts, host+":8080", "/", nil))
	readAll(get(t, ts, host+":8080", "/main.js", nil)) // cache hit
	readAll(get(t, ts, "nope.example.com", "/", nil))  // notfound
	readAll(get(t, ts, host+":8080", "/__oa/bridge.js", nil))

	out := readAll(get(t, ts, "any", "/__oa/metrics", nil))
	for _, want := range []string{
		`oa_gateway_requests_total{kind="sandbox",status="200"} 2`,
		`oa_gateway_requests_total{kind="notfound",status="404"} 1`,
		`oa_gateway_requests_total{kind="bridge",status="200"} 1`,
		`oa_gateway_resolve_cache_total{result="hit"} 1`,
		`oa_gateway_request_duration_seconds_bucket{kind="sandbox",le="+Inf"} 2`,
		`oa_gateway_resolve_duration_seconds_count{result="ok"}`,
	} {
		if !strings.Contains(out, want) {
			t.Errorf("metrics missing %q\n%s", want, out)
		}
	}
	gw.cfg.MetricsToken = "tok"
	if r := get(t, ts, "any", "/__oa/metrics", nil); r.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401 without token, got %d", r.StatusCode)
	}
	if r := get(t, ts, "any", "/__oa/metrics", map[string]string{"Authorization": "Bearer tok"}); r.StatusCode != 200 {
		t.Fatalf("expected 200 with token, got %d", r.StatusCode)
	}
}
