package main

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"path"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

//go:embed assets/bridge.js
var bridgeJS []byte

const (
	resolveTTL   = 2 * time.Second
	cookieName   = "oa_preview"
	bridgeScript = `<script src="/__oa/bridge.js" defer></script>`
)

type Config struct {
	Listen        string
	APIURL        string
	InternalToken string
	PublicWebURL  string
	AppDomain     string // host[:port]; only the host part is used for matching
	MetricsToken  string // optional bearer token for /__oa/metrics
}

// Resolution mirrors the api's /internal/resolve response.
type Resolution struct {
	Kind     string `json:"kind"` // sandbox|waking|auth_required|static|notfound
	Upstream string `json:"upstream,omitempty"`
	Base     string `json:"base,omitempty"`
	ChatID   string `json:"chat_id,omitempty"` // optional (custom domains)
	Env      string `json:"env,omitempty"`     // optional
}

// HostInfo is what can be derived from the Host header alone.
type HostInfo struct {
	Kind    string // preview|deploy|""
	Port    int
	ChatID  string
	UserID  string
	Seq     int
	Version string
}

func (h HostInfo) Env() string {
	if h.Kind == "deploy" {
		return "prod"
	}
	return "dev"
}

type cacheEntry struct {
	res Resolution
	exp time.Time
}

type Gateway struct {
	cfg       Config
	domain    string
	previewRe *regexp.Regexp
	deployRe  *regexp.Regexp
	api       *url.URL
	client    *http.Client
	bridge    []byte
	csp       string

	mu      sync.Mutex
	cache   map[string]cacheEntry
	proxies map[string]*httputil.ReverseProxy
	apiProx *httputil.ReverseProxy
	metrics *metrics
}

func New(cfg Config) (*Gateway, error) {
	api, err := url.Parse(strings.TrimRight(cfg.APIURL, "/"))
	if err != nil || api.Scheme == "" {
		return nil, fmt.Errorf("invalid OA_API_URL %q", cfg.APIURL)
	}
	domain := strings.ToLower(stripPort(cfg.AppDomain))
	qd := regexp.QuoteMeta(domain)
	g := &Gateway{
		cfg:       cfg,
		domain:    domain,
		previewRe: regexp.MustCompile(`^(\d+)-([0-9a-f]{32})-preview\.` + qd + `$`),
		deployRe:  regexp.MustCompile(`^(\d+)-([0-9a-f]{32})-(\d+)-(latest|v\d+)\.` + qd + `$`),
		api:       api,
		client:    &http.Client{Timeout: 30 * time.Second},
		cache:     map[string]cacheEntry{},
		proxies:   map[string]*httputil.ReverseProxy{},
		metrics:   newMetrics(),
	}
	ancestors := []string{"'self'", "http://localhost:3000"}
	if w := strings.TrimRight(cfg.PublicWebURL, "/"); w != "" && w != "http://localhost:3000" {
		ancestors = append(ancestors, w)
	}
	g.csp = "frame-ancestors " + strings.Join(ancestors, " ")
	home, _ := json.Marshal(strings.TrimRight(cfg.PublicWebURL, "/"))
	g.bridge = append([]byte("window.__OA_HOME="+string(home)+";\n"), bridgeJS...)
	g.apiProx = g.newAPIProxy()
	return g, nil
}

func stripPort(h string) string {
	if host, _, err := net.SplitHostPort(h); err == nil {
		return host
	}
	return h
}

func (g *Gateway) parseHost(host string) HostInfo {
	h := strings.ToLower(stripPort(host))
	if m := g.previewRe.FindStringSubmatch(h); m != nil {
		port, _ := strconv.Atoi(m[1])
		return HostInfo{Kind: "preview", Port: port, ChatID: m[2]}
	}
	if m := g.deployRe.FindStringSubmatch(h); m != nil {
		seq, _ := strconv.Atoi(m[3])
		return HostInfo{Kind: "deploy", UserID: m[1], ChatID: m[2], Seq: seq, Version: m[4]}
	}
	return HostInfo{}
}

func (g *Gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/__oa/metrics":
		g.serveMetrics(w, r)
		return
	case "/__oa/health":
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
		return
	}
	g.instrument(w, r, g.route)
}

func (g *Gateway) route(w http.ResponseWriter, r *http.Request) {
	switch {
	case r.URL.Path == "/__oa/bridge.js":
		setKind(w, "bridge")
		g.serveBridge(w, r)
	case r.URL.Path == "/__preview_auth":
		setKind(w, "preview_auth")
		g.servePreviewAuth(w, r)
	case r.URL.Path == "/api" || strings.HasPrefix(r.URL.Path, "/api/"):
		setKind(w, "app_api")
		g.serveAppAPI(w, r)
	default:
		g.serveApp(w, r)
	}
}

func (g *Gateway) serveBridge(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=300")
	_, _ = w.Write(g.bridge)
}

// ---------------------------------------------------------------- resolve

func (g *Gateway) resolve(r *http.Request) (Resolution, error) {
	cookie := ""
	if c, err := r.Cookie(cookieName); err == nil {
		cookie = c.Value
	}
	key := strings.ToLower(stripPort(r.Host)) + "|" + cookie
	g.mu.Lock()
	if e, ok := g.cache[key]; ok && time.Now().Before(e.exp) {
		g.mu.Unlock()
		g.metrics.inc("oa_gateway_resolve_cache_total", labels("result", "hit"))
		return e.res, nil
	}
	g.mu.Unlock()
	g.metrics.inc("oa_gateway_resolve_cache_total", labels("result", "miss"))
	t0 := time.Now()
	res, err := g.resolveRemote(r, cookie, key)
	result := "ok"
	if err != nil {
		result = "error"
	}
	g.metrics.observe("oa_gateway_resolve_duration_seconds", labels("result", result), time.Since(t0).Seconds())
	return res, err
}

func (g *Gateway) resolveRemote(r *http.Request, cookie, key string) (Resolution, error) {

	q := url.Values{"host": {r.Host}, "path": {r.URL.Path}, "cookie": {cookie}}
	req, _ := http.NewRequestWithContext(r.Context(), http.MethodGet, g.api.String()+"/internal/resolve?"+q.Encode(), nil)
	req.Header.Set("X-Internal-Token", g.cfg.InternalToken)
	resp, err := g.client.Do(req)
	if err != nil {
		return Resolution{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Resolution{}, fmt.Errorf("resolve: api status %d", resp.StatusCode)
	}
	var res Resolution
	if err := json.NewDecoder(resp.Body).Decode(&res); err != nil {
		return Resolution{}, fmt.Errorf("resolve: %w", err)
	}
	// Transitional states must be re-checked promptly; stable ones are cached.
	if res.Kind != "waking" {
		g.mu.Lock()
		if len(g.cache) > 10000 {
			g.cache = map[string]cacheEntry{}
		}
		g.cache[key] = cacheEntry{res: res, exp: time.Now().Add(resolveTTL)}
		g.mu.Unlock()
	}
	return res, nil
}

func (g *Gateway) invalidate(host string) {
	prefix := strings.ToLower(stripPort(host)) + "|"
	g.mu.Lock()
	for k := range g.cache {
		if strings.HasPrefix(k, prefix) {
			delete(g.cache, k)
		}
	}
	g.mu.Unlock()
}

// ---------------------------------------------------------------- app traffic

func (g *Gateway) serveApp(w http.ResponseWriter, r *http.Request) {
	res, err := g.resolve(r)
	if err != nil {
		log.Printf("resolve %s: %v", r.Host, err)
		setKind(w, "error")
		writePage(w, http.StatusBadGateway, pageUnavailable)
		return
	}
	setKind(w, res.Kind)
	switch res.Kind {
	case "sandbox":
		p, err := g.proxyFor(res.Upstream)
		if err != nil {
			writePage(w, http.StatusBadGateway, pageUnavailable)
			return
		}
		p.ServeHTTP(w, r)
	case "static":
		g.serveStatic(w, r, res.Base)
	case "waking":
		w.Header().Set("Retry-After", "2")
		writePage(w, http.StatusServiceUnavailable, pageWaking)
	case "auth_required":
		writePage(w, http.StatusForbidden, pageForbidden)
	default:
		writePage(w, http.StatusNotFound, pageNotFound)
	}
}

func (g *Gateway) proxyFor(upstream string) (*httputil.ReverseProxy, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if p, ok := g.proxies[upstream]; ok {
		return p, nil
	}
	target, err := url.Parse(upstream)
	if err != nil || target.Host == "" {
		return nil, errors.New("invalid upstream")
	}
	p := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(target)
			pr.Out.Host = pr.In.Host // dev servers see the public preview host
			pr.SetXForwarded()
			limitEncoding(pr.Out)
		},
		ModifyResponse: g.modifyResponse,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			// Dev server restarting / sandbox gone: show the waking page and re-resolve soon.
			log.Printf("proxy %s -> %s: %v", r.Host, upstream, err)
			g.invalidate(r.Host)
			w.Header().Set("Retry-After", "2")
			writePage(w, http.StatusServiceUnavailable, pageWaking)
		},
		FlushInterval: -1, // stream SSE / chunked responses immediately
	}
	g.proxies[upstream] = p
	return p, nil
}

// limitEncoding restricts upstream compression to gzip, which we can rewrite.
func limitEncoding(h *http.Request) {
	if strings.Contains(h.Header.Get("Accept-Encoding"), "gzip") {
		h.Header.Set("Accept-Encoding", "gzip")
	} else {
		h.Header.Del("Accept-Encoding")
	}
}

func (g *Gateway) modifyResponse(resp *http.Response) error {
	if !isHTML(resp.Header) || resp.Request.Method == http.MethodHead {
		return nil
	}
	return injectResponse(resp, g.csp)
}

// ---------------------------------------------------------------- static deploys

var assetExt = regexp.MustCompile(`\.[a-zA-Z0-9]{1,8}$`)

func (g *Gateway) serveStatic(w http.ResponseWriter, r *http.Request, base string) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	p := path.Clean("/" + r.URL.Path)
	if p == "/" {
		p = "/index.html"
	}
	resp, err := g.fetchStatic(r, base, p)
	if err == nil && resp.StatusCode == http.StatusNotFound && !assetExt.MatchString(p) {
		resp.Body.Close()
		resp, err = g.fetchStatic(r, base, "/index.html") // SPA fallback
	}
	if err != nil {
		log.Printf("static %s%s: %v", base, p, err)
		writePage(w, http.StatusBadGateway, pageUnavailable)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		writePage(w, http.StatusNotFound, pageNotFound)
		return
	}
	if resp.StatusCode != http.StatusOK {
		writePage(w, http.StatusBadGateway, pageUnavailable)
		return
	}
	if isHTML(resp.Header) {
		if err := injectResponse(resp, g.csp); err != nil {
			writePage(w, http.StatusBadGateway, pageUnavailable)
			return
		}
		resp.Header.Set("Cache-Control", "no-cache")
	} else if strings.HasPrefix(p, "/assets/") {
		resp.Header.Set("Cache-Control", "public, max-age=31536000, immutable")
	} else {
		resp.Header.Set("Cache-Control", "public, max-age=300")
	}
	for _, k := range []string{"Content-Type", "Content-Length", "Content-Encoding", "Cache-Control", "Content-Security-Policy", "ETag", "Last-Modified"} {
		if v := resp.Header.Get(k); v != "" {
			w.Header().Set(k, v)
		}
	}
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.WriteHeader(http.StatusOK)
	if r.Method != http.MethodHead {
		_, _ = io.Copy(w, resp.Body)
	}
}

func (g *Gateway) fetchStatic(r *http.Request, base, p string) (*http.Response, error) {
	u := strings.TrimRight(base, "/") + p
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Internal-Token", g.cfg.InternalToken)
	if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		req.Header.Set("Accept-Encoding", "gzip")
	}
	return g.client.Do(req)
}

// ---------------------------------------------------------------- preview auth

func safeRedirect(target string) string {
	if target == "" || !strings.HasPrefix(target, "/") || strings.HasPrefix(target, "//") || strings.HasPrefix(target, "/\\") {
		return "/"
	}
	return target
}

func (g *Gateway) servePreviewAuth(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	ticket := q.Get("ticket")
	if ticket == "" {
		writePage(w, http.StatusForbidden, pageForbidden)
		return
	}
	body, _ := json.Marshal(map[string]string{"ticket": ticket, "host": r.Host})
	req, _ := http.NewRequestWithContext(r.Context(), http.MethodPost, g.api.String()+"/internal/preview-auth", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Internal-Token", g.cfg.InternalToken)
	resp, err := g.client.Do(req)
	if err != nil {
		writePage(w, http.StatusBadGateway, pageUnavailable)
		return
	}
	defer resp.Body.Close()
	var out struct {
		Cookie string `json:"cookie"`
		MaxAge int    `json:"max_age"`
	}
	if resp.StatusCode != http.StatusOK || json.NewDecoder(resp.Body).Decode(&out) != nil || out.Cookie == "" {
		writePage(w, http.StatusForbidden, pageForbidden)
		return
	}
	// Browsers treat http://*.localhost as a secure context, so dev previews can use the same
	// cross-site iframe cookie as production: SameSite=None + Secure + Partitioned (CHIPS).
	secure := r.TLS != nil || strings.EqualFold(r.Header.Get("X-Forwarded-Proto"), "https") || isLocalhost(r.Host)
	c := &http.Cookie{Name: cookieName, Value: out.Cookie, Path: "/", HttpOnly: true, MaxAge: out.MaxAge, Secure: secure}
	if secure {
		c.SameSite = http.SameSiteNoneMode // allow inside the workbench iframe
		c.Partitioned = true
	} else {
		c.SameSite = http.SameSiteLaxMode
	}
	http.SetCookie(w, c)
	g.invalidate(r.Host)
	http.Redirect(w, r, safeRedirect(q.Get("redirect")), http.StatusFound)
}

// ---------------------------------------------------------------- app api (Cloud BaaS)

func (g *Gateway) newAPIProxy() *httputil.ReverseProxy {
	return &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(g.api)
			// SetURL joins paths: /api/v1/x -> {api}/api/v1/x; we need /apps/api/v1/x.
			pr.Out.URL.Path = strings.TrimRight(g.api.Path, "/") + "/apps" + pr.In.URL.Path
			pr.Out.URL.RawPath = ""
			pr.SetXForwarded()
		},
		FlushInterval: -1,
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, err error) {
			log.Printf("app api proxy: %v", err)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadGateway)
			_, _ = w.Write([]byte(`{"detail":"backend unavailable"}`))
		},
	}
}

func (g *Gateway) serveAppAPI(w http.ResponseWriter, r *http.Request) {
	info := g.parseHost(r.Host)
	chatID, env := info.ChatID, info.Env()
	if chatID == "" { // custom domain: ask the api
		res, err := g.resolve(r)
		if err != nil || res.ChatID == "" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"detail":"unknown app"}`))
			return
		}
		chatID = res.ChatID
		if res.Env != "" {
			env = res.Env
		} else {
			env = "prod"
		}
	}
	// Never trust client-supplied routing headers.
	for _, h := range []string{"X-OA-App-Chat", "X-OA-App-Env", "App-Host", "X-Internal-Token"} {
		r.Header.Del(h)
	}
	r.Header.Set("X-OA-App-Chat", chatID)
	r.Header.Set("X-OA-App-Env", env)
	r.Header.Set("App-Host", r.Host)
	g.apiProx.ServeHTTP(w, r)
}

func isLocalhost(hostport string) bool {
	h := strings.ToLower(stripPort(hostport))
	return h == "localhost" || strings.HasSuffix(h, ".localhost")
}
