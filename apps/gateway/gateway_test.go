package main

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

const chat = "0123456789abcdef0123456789abcdef"

type fakeAPI struct {
	srv          *httptest.Server
	resolveCalls atomic.Int32
	res          map[string]Resolution // host(without port) -> resolution
	lastAppReq   *http.Request
	lastAppBody  string
}

func newFakeAPI(t *testing.T) *fakeAPI {
	f := &fakeAPI{res: map[string]Resolution{}}
	mux := http.NewServeMux()
	mux.HandleFunc("/internal/resolve", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Internal-Token") != "itok" {
			w.WriteHeader(401)
			return
		}
		f.resolveCalls.Add(1)
		host := stripPort(r.URL.Query().Get("host"))
		res, ok := f.res[host]
		if !ok {
			res = Resolution{Kind: "notfound"}
		}
		if res.Kind == "auth_required" && r.URL.Query().Get("cookie") == "good-cookie" {
			res = f.res["authed:"+host]
		}
		_ = json.NewEncoder(w).Encode(res)
	})
	mux.HandleFunc("/internal/static/", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Internal-Token") != "itok" {
			w.WriteHeader(401)
			return
		}
		switch strings.TrimPrefix(r.URL.Path, "/internal/static/"+chat+"/v1") {
		case "/index.html":
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			io.WriteString(w, "<html><body><div id=root></div></body></html>")
		case "/assets/app.js":
			w.Header().Set("Content-Type", "application/javascript")
			io.WriteString(w, "console.log(1)")
		default:
			http.NotFound(w, r)
		}
	})
	mux.HandleFunc("/internal/preview-auth", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["ticket"] != "t1" || body["host"] == "" {
			w.WriteHeader(403)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"cookie": "good-cookie", "max_age": 3600})
	})
	mux.HandleFunc("/apps/api/", func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		f.lastAppReq, f.lastAppBody = r, string(b)
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"ok":true}`)
	})
	f.srv = httptest.NewServer(mux)
	t.Cleanup(f.srv.Close)
	return f
}

func newSandbox(t *testing.T) *httptest.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("X-Seen-Host", r.Host)
		html := "<!doctype html><html><head></head><BODY><h1>hi</h1></BODY></html>"
		if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			w.Header().Set("Content-Encoding", "gzip")
			zw := gzip.NewWriter(w)
			io.WriteString(zw, html)
			zw.Close()
			return
		}
		io.WriteString(w, html)
	})
	mux.HandleFunc("/main.js", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/javascript")
		io.WriteString(w, "export {}")
	})
	// Minimal raw upgrade endpoint (like Vite HMR) – echoes one line.
	mux.HandleFunc("/hmr", func(w http.ResponseWriter, r *http.Request) {
		if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
			w.WriteHeader(400)
			return
		}
		conn, rw, _ := w.(http.Hijacker).Hijack()
		defer conn.Close()
		rw.WriteString("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
		rw.Flush()
		line, _ := rw.ReadString('\n')
		rw.WriteString("echo:" + line)
		rw.Flush()
	})
	s := httptest.NewServer(mux)
	t.Cleanup(s.Close)
	return s
}

func setup(t *testing.T) (*fakeAPI, *httptest.Server, *Gateway) {
	api := newFakeAPI(t)
	gw, err := New(Config{APIURL: api.srv.URL, InternalToken: "itok", PublicWebURL: "https://oa.example", AppDomain: "app.localhost:8080"})
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(gw)
	t.Cleanup(ts.Close)
	return api, ts, gw
}

func get(t *testing.T, ts *httptest.Server, host, path string, hdr map[string]string) *http.Response {
	req, _ := http.NewRequest("GET", ts.URL+path, nil)
	req.Host = host
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	resp, err := (&http.Transport{DisableCompression: true}).RoundTrip(req)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func readAll(r *http.Response) string {
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	return string(b)
}

func TestParseHost(t *testing.T) {
	_, _, gw := setup(t)
	h := gw.parseHost("5173-" + chat + "-preview.app.localhost:8080")
	if h.Kind != "preview" || h.Port != 5173 || h.ChatID != chat || h.Env() != "dev" {
		t.Fatalf("%+v", h)
	}
	h = gw.parseHost("42-" + chat + "-3-latest.APP.localhost")
	if h.Kind != "deploy" || h.UserID != "42" || h.Seq != 3 || h.Version != "latest" || h.Env() != "prod" {
		t.Fatalf("%+v", h)
	}
	if gw.parseHost("evil.com").Kind != "" || gw.parseHost("5173-"+chat+"-preview.app.localhost.evil.com").Kind != "" {
		t.Fatal("should not match")
	}
}

func TestSandboxProxyInjectsBridgeAndCaches(t *testing.T) {
	api, ts, _ := setup(t)
	sb := newSandbox(t)
	host := "5173-" + chat + "-preview.app.localhost"
	api.res[host] = Resolution{Kind: "sandbox", Upstream: sb.URL}

	for _, enc := range []string{"gzip", ""} {
		resp := get(t, ts, host+":8080", "/", map[string]string{"Accept-Encoding": enc})
		body := readAll(resp)
		if resp.StatusCode != 200 || !strings.Contains(body, bridgeScript+"</BODY>") {
			t.Fatalf("enc=%q status=%d body=%q", enc, resp.StatusCode, body)
		}
		if resp.Header.Get("Content-Encoding") != "" || resp.Header.Get("X-Frame-Options") != "" {
			t.Fatalf("headers %v", resp.Header)
		}
		if csp := resp.Header.Get("Content-Security-Policy"); csp != "frame-ancestors 'self' http://localhost:3000 https://oa.example" {
			t.Fatalf("csp %q", csp)
		}
		if resp.Header.Get("X-Seen-Host") != host+":8080" {
			t.Fatalf("host not preserved: %q", resp.Header.Get("X-Seen-Host"))
		}
	}
	js := get(t, ts, host, "/main.js", nil)
	if b := readAll(js); b != "export {}" {
		t.Fatalf("non-html modified: %q", b)
	}
	if n := api.resolveCalls.Load(); n != 1 {
		t.Fatalf("resolve should be cached, calls=%d", n)
	}
}

func TestWebSocketUpgrade(t *testing.T) {
	api, ts, _ := setup(t)
	sb := newSandbox(t)
	host := "5173-" + chat + "-preview.app.localhost"
	api.res[host] = Resolution{Kind: "sandbox", Upstream: sb.URL}

	conn, err := net.Dial("tcp", strings.TrimPrefix(ts.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	io.WriteString(conn, "GET /hmr HTTP/1.1\r\nHost: "+host+"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
	br := bufio.NewReader(conn)
	resp, err := http.ReadResponse(br, nil)
	if err != nil || resp.StatusCode != 101 {
		t.Fatalf("upgrade failed: %v %v", resp, err)
	}
	io.WriteString(conn, "ping\n")
	line, _ := br.ReadString('\n')
	if line != "echo:ping\n" {
		t.Fatalf("got %q", line)
	}
}

func TestUpstreamDownShowsWaking(t *testing.T) {
	api, ts, _ := setup(t)
	host := "5173-" + chat + "-preview.app.localhost"
	api.res[host] = Resolution{Kind: "sandbox", Upstream: "http://127.0.0.1:1"}
	resp := get(t, ts, host, "/", nil)
	body := readAll(resp)
	if resp.StatusCode != 503 || !strings.Contains(body, `http-equiv="refresh" content="2"`) {
		t.Fatalf("%d %s", resp.StatusCode, body)
	}
}

func TestStatesPages(t *testing.T) {
	api, ts, _ := setup(t)
	api.res["1-"+chat+"-preview.app.localhost"] = Resolution{Kind: "waking"}
	api.res["2-"+chat+"-preview.app.localhost"] = Resolution{Kind: "auth_required"}
	cases := map[string]int{"1-" + chat + "-preview.app.localhost": 503, "2-" + chat + "-preview.app.localhost": 403, "unknown.app.localhost": 404}
	for host, want := range cases {
		resp := get(t, ts, host, "/", nil)
		readAll(resp)
		if resp.StatusCode != want {
			t.Fatalf("%s: got %d want %d", host, resp.StatusCode, want)
		}
	}
	// waking is never cached
	get(t, ts, "1-"+chat+"-preview.app.localhost", "/", nil).Body.Close()
	if api.resolveCalls.Load() != 4 {
		t.Fatalf("waking should not be cached: %d", api.resolveCalls.Load())
	}
}

func TestStaticDeploySPA(t *testing.T) {
	api, ts, _ := setup(t)
	host := "7-" + chat + "-1-v1.app.localhost"
	api.res[host] = Resolution{Kind: "static", Base: api.srv.URL + "/internal/static/" + chat + "/v1"}

	for _, p := range []string{"/", "/todos/42"} {
		resp := get(t, ts, host, p, nil)
		body := readAll(resp)
		if resp.StatusCode != 200 || !strings.Contains(body, bridgeScript) || resp.Header.Get("Cache-Control") != "no-cache" {
			t.Fatalf("%s: %d %q %v", p, resp.StatusCode, body, resp.Header)
		}
	}
	resp := get(t, ts, host, "/assets/app.js", nil)
	if b := readAll(resp); b != "console.log(1)" || !strings.Contains(resp.Header.Get("Cache-Control"), "immutable") {
		t.Fatalf("asset %q %v", b, resp.Header)
	}
	resp = get(t, ts, host, "/missing.png", nil)
	readAll(resp)
	if resp.StatusCode != 404 {
		t.Fatalf("missing asset should 404, got %d", resp.StatusCode)
	}
}

func TestPreviewAuth(t *testing.T) {
	api, ts, _ := setup(t)
	sb := newSandbox(t)
	host := "5173-" + chat + "-preview.app.localhost"
	api.res[host] = Resolution{Kind: "auth_required"}
	api.res["authed:"+host] = Resolution{Kind: "sandbox", Upstream: sb.URL}

	resp := get(t, ts, host, "/__preview_auth?ticket=bad&redirect=/", nil)
	readAll(resp)
	if resp.StatusCode != 403 {
		t.Fatalf("bad ticket: %d", resp.StatusCode)
	}
	resp = get(t, ts, host, "/__preview_auth?ticket=t1&redirect=//evil.com", nil)
	readAll(resp)
	if resp.StatusCode != 302 || resp.Header.Get("Location") != "/" {
		t.Fatalf("open redirect: %d %q", resp.StatusCode, resp.Header.Get("Location"))
	}
	ck := resp.Cookies()
	if len(ck) != 1 || ck[0].Name != cookieName || ck[0].Value != "good-cookie" || !ck[0].HttpOnly || ck[0].SameSite != http.SameSiteNoneMode || !ck[0].Partitioned {
		t.Fatalf("cookie %+v", ck)
	}
	resp = get(t, ts, host, "/", map[string]string{"Cookie": cookieName + "=good-cookie"})
	if b := readAll(resp); resp.StatusCode != 200 || !strings.Contains(b, "<h1>hi</h1>") {
		t.Fatalf("authed request: %d", resp.StatusCode)
	}
}

func TestAppAPIProxy(t *testing.T) {
	api, ts, _ := setup(t)
	req, _ := http.NewRequest("POST", ts.URL+"/api/v1/entities/Todo?x=1", bytes.NewBufferString(`{"title":"a"}`))
	req.Host = "9-" + chat + "-2-latest.app.localhost:8080"
	req.Header.Set("X-OA-App-Chat", "spoofed")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	readAll(resp)
	r := api.lastAppReq
	if resp.StatusCode != 200 || r == nil {
		t.Fatalf("status %d", resp.StatusCode)
	}
	if r.URL.Path != "/apps/api/v1/entities/Todo" || r.URL.RawQuery != "x=1" || api.lastAppBody != `{"title":"a"}` {
		t.Fatalf("path %s?%s body %q", r.URL.Path, r.URL.RawQuery, api.lastAppBody)
	}
	if r.Header.Get("X-OA-App-Chat") != chat || r.Header.Get("X-OA-App-Env") != "prod" || r.Header.Get("App-Host") != req.Host {
		t.Fatalf("headers %v", r.Header)
	}

	req, _ = http.NewRequest("GET", ts.URL+"/api/v1/config", nil)
	req.Host = "5173-" + chat + "-preview.app.localhost"
	resp, _ = http.DefaultClient.Do(req)
	readAll(resp)
	if api.lastAppReq.Header.Get("X-OA-App-Env") != "dev" {
		t.Fatal("preview should be dev env")
	}
}

func TestBridgeServed(t *testing.T) {
	_, ts, _ := setup(t)
	resp := get(t, ts, "anything", "/__oa/bridge.js", nil)
	body := readAll(resp)
	if !strings.HasPrefix(body, `window.__OA_HOME="https://oa.example";`) || len(body) < 1000 {
		t.Fatalf("bridge %q", body[:min(80, len(body))])
	}
	if !strings.HasPrefix(resp.Header.Get("Content-Type"), "application/javascript") {
		t.Fatal(resp.Header)
	}
}

func TestInjectHTML(t *testing.T) {
	if got := string(injectHTML([]byte("<p>no body"))); got != "<p>no body"+bridgeScript {
		t.Fatal(got)
	}
	once := injectHTML([]byte("<body></body>"))
	if string(injectHTML(once)) != string(once) {
		t.Fatal("double injection")
	}
}
