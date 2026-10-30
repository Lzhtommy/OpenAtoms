package main

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// Server wires all sandboxd handlers.
type Server struct {
	root   string
	token  string
	mux    *http.ServeMux
	procs  *Supervisor
	kernel *Kernel
}

func NewServer(root, token string) *Server {
	s := &Server{root: root, token: token, mux: http.NewServeMux()}
	s.procs = NewSupervisor(s)
	s.kernel = NewKernel(root, s.baseEnv)

	s.mux.HandleFunc("GET /health", s.handleHealth)
	s.mux.HandleFunc("POST /exec", s.handleExec)
	s.mux.HandleFunc("GET /fs/list", s.handleFsList)
	s.mux.HandleFunc("GET /fs/read", s.handleFsRead)
	s.mux.HandleFunc("GET /fs/stat", s.handleFsStat)
	s.mux.HandleFunc("POST /fs/write", s.handleFsWrite)
	s.mux.HandleFunc("POST /fs/mkdir", s.handleFsMkdir)
	s.mux.HandleFunc("POST /fs/rm", s.handleFsRm)
	s.mux.HandleFunc("POST /fs/mv", s.handleFsMv)
	s.mux.HandleFunc("POST /process/ensure", s.handleProcEnsure)
	s.mux.HandleFunc("POST /process/stop", s.handleProcStop)
	s.mux.HandleFunc("GET /process/list", s.handleProcList)
	s.mux.HandleFunc("GET /process/logs", s.handleProcLogs)
	s.mux.HandleFunc("GET /ports", s.handlePorts)
	s.mux.HandleFunc("POST /kernel/execute", s.handleKernelExec)
	s.mux.HandleFunc("POST /kernel/restart", s.handleKernelRestart)
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/health" && s.token != "" {
		got := r.Header.Get("X-Sandbox-Token")
		if got == "" {
			got = r.URL.Query().Get("token")
		}
		if subtle.ConstantTimeCompare([]byte(got), []byte(s.token)) != 1 {
			writeErr(w, http.StatusUnauthorized, "unauthorized")
			return
		}
	}
	s.mux.ServeHTTP(w, r)
}

// Shutdown stops supervised processes and the kernel.
func (s *Server) Shutdown() {
	s.procs.StopAll()
	s.kernel.Close()
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "version": Version, "root": s.root})
}

// resolve maps a client path to an absolute path inside root.
// Accepts root-relative paths, absolute paths under root, and the canonical
// in-container prefix "/workspace" (so agents can always use /workspace/... paths,
// even when sandboxd runs on the host in local mode).
func (s *Server) resolve(p string) (string, error) {
	p = strings.TrimSpace(p)
	if p == "" || p == "." || p == "/" {
		return s.root, nil
	}
	var rel string
	switch {
	case filepath.IsAbs(p) && (p == s.root || strings.HasPrefix(p, s.root+string(filepath.Separator))):
		rel, _ = filepath.Rel(s.root, p)
	case p == "/workspace" || strings.HasPrefix(p, "/workspace/"):
		rel = strings.TrimPrefix(strings.TrimPrefix(p, "/workspace"), "/")
	default:
		rel = strings.TrimPrefix(p, "/")
	}
	full := filepath.Join(s.root, filepath.Clean("/"+rel))
	if full != s.root && !strings.HasPrefix(full, s.root+string(filepath.Separator)) {
		return "", errors.New("path escapes workspace")
	}
	// Reject traversal through symlinks that point outside root.
	if real, err := evalExistingPrefix(full); err == nil {
		if real != s.root && !strings.HasPrefix(real, s.root+string(filepath.Separator)) {
			return "", errors.New("path escapes workspace via symlink")
		}
	}
	return full, nil
}

// evalExistingPrefix resolves symlinks for the longest existing prefix of p.
func evalExistingPrefix(p string) (string, error) {
	cur := p
	var tail []string
	for {
		if _, err := os.Lstat(cur); err == nil {
			real, err := filepath.EvalSymlinks(cur)
			if err != nil {
				return "", err
			}
			for i := len(tail) - 1; i >= 0; i-- {
				real = filepath.Join(real, tail[i])
			}
			return real, nil
		}
		parent := filepath.Dir(cur)
		if parent == cur {
			return p, nil
		}
		tail = append(tail, filepath.Base(cur))
		cur = parent
	}
}

func (s *Server) rel(full string) string {
	r, err := filepath.Rel(s.root, full)
	if err != nil {
		return full
	}
	return filepath.ToSlash(r)
}

// baseEnv returns the environment for child processes: inherited env with a
// PATH guaranteed to contain common node/pnpm/python locations.
func (s *Server) baseEnv(extra map[string]string) []string {
	env := os.Environ()
	m := make(map[string]int, len(env))
	for i, kv := range env {
		if k, _, ok := strings.Cut(kv, "="); ok {
			m[k] = i
		}
	}
	set := func(k, v string) {
		if i, ok := m[k]; ok {
			env[i] = k + "=" + v
		} else {
			m[k] = len(env)
			env = append(env, k+"="+v)
		}
	}
	path := os.Getenv("PATH")
	for _, d := range []string{"/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin", s.root + "/node_modules/.bin"} {
		if !strings.Contains(":"+path+":", ":"+d+":") {
			path += ":" + d
		}
	}
	set("PATH", path)
	set("CI", "1")
	set("FORCE_COLOR", "0")
	for k, v := range extra {
		set(k, v)
	}
	return env
}

// ---- helpers

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}

func decode(r *http.Request, v any) error {
	dec := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 256<<20))
	if err := dec.Decode(v); err != nil {
		return fmt.Errorf("invalid json: %w", err)
	}
	return nil
}

// sseWriter serializes JSON events onto a text/event-stream response.
type sseWriter struct {
	mu sync.Mutex
	w  http.ResponseWriter
	f  http.Flusher
}

func newSSE(w http.ResponseWriter) *sseWriter {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	f, _ := w.(http.Flusher)
	sw := &sseWriter{w: w, f: f}
	sw.flush()
	return sw
}

func (s *sseWriter) send(v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := fmt.Fprintf(s.w, "data: %s\n\n", b); err != nil {
		return err
	}
	s.flush()
	return nil
}

func (s *sseWriter) flush() {
	if s.f != nil {
		s.f.Flush()
	}
}
