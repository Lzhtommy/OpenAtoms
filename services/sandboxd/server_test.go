package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

const tok = "test-token"

func newTestServer(t *testing.T) (*httptest.Server, string) {
	t.Helper()
	root, _ := filepath.EvalSymlinks(t.TempDir())
	s := NewServer(root, tok)
	ts := httptest.NewServer(s)
	t.Cleanup(func() { ts.Close(); s.Shutdown() })
	return ts, root
}

func do(t *testing.T, ts *httptest.Server, method, path string, body any) *http.Response {
	t.Helper()
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, ts.URL+path, rd)
	req.Header.Set("X-Sandbox-Token", tok)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func jsonBody[T any](t *testing.T, resp *http.Response) T {
	t.Helper()
	defer resp.Body.Close()
	var v T
	if err := json.NewDecoder(resp.Body).Decode(&v); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return v
}

type sseEvent map[string]any

func readSSE(t *testing.T, resp *http.Response) []sseEvent {
	t.Helper()
	defer resp.Body.Close()
	var out []sseEvent
	sc := bufio.NewScanner(resp.Body)
	for sc.Scan() {
		line := sc.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var e sseEvent
		if err := json.Unmarshal([]byte(line[6:]), &e); err != nil {
			t.Fatal(err)
		}
		out = append(out, e)
	}
	return out
}

func execCollect(t *testing.T, ts *httptest.Server, body map[string]any) (stdout, stderr string, exit sseEvent) {
	evs := readSSE(t, do(t, ts, "POST", "/exec", body))
	for _, e := range evs {
		switch e["type"] {
		case "stdout":
			stdout += e["data"].(string)
		case "stderr":
			stderr += e["data"].(string)
		case "exit":
			exit = e
		}
	}
	return
}

func TestHealthAndAuth(t *testing.T) {
	ts, root := newTestServer(t)
	resp, _ := http.Get(ts.URL + "/health")
	h := jsonBody[map[string]any](t, resp)
	if h["ok"] != true || h["root"] != root {
		t.Fatalf("health: %v", h)
	}
	resp, _ = http.Get(ts.URL + "/fs/list")
	if resp.StatusCode != 401 {
		t.Fatalf("want 401, got %d", resp.StatusCode)
	}
}

func TestExec(t *testing.T) {
	ts, root := newTestServer(t)
	out, errOut, exit := execCollect(t, ts, map[string]any{"cmd": "echo hi; echo err >&2; pwd; echo $FOO; exit 3", "env": map[string]string{"FOO": "bar"}})
	if !strings.Contains(out, "hi\n") || !strings.Contains(out, root) || !strings.Contains(out, "bar") {
		t.Fatalf("stdout: %q", out)
	}
	if errOut != "err\n" {
		t.Fatalf("stderr: %q", errOut)
	}
	if exit["code"].(float64) != 3 || exit["timed_out"] != false {
		t.Fatalf("exit: %v", exit)
	}

	os.MkdirAll(filepath.Join(root, "sub"), 0o755)
	out, _, _ = execCollect(t, ts, map[string]any{"cmd": "pwd", "cwd": "sub"})
	if strings.TrimSpace(out) != filepath.Join(root, "sub") {
		t.Fatalf("cwd: %q", out)
	}
	_, _, exit = execCollect(t, ts, map[string]any{"cmd": "pwd", "cwd": "../.."})
	if exit["code"].(float64) != 0 {
		t.Fatal("cwd clamped to root expected to succeed")
	}
}

func TestExecTimeoutKillsGroup(t *testing.T) {
	ts, _ := newTestServer(t)
	start := time.Now()
	_, _, exit := execCollect(t, ts, map[string]any{"cmd": "sleep 30 & sleep 30", "timeout_ms": 500})
	if exit["timed_out"] != true || exit["code"].(float64) != 124 {
		t.Fatalf("exit: %v", exit)
	}
	if time.Since(start) > 5*time.Second {
		t.Fatal("timeout not enforced")
	}
}

func TestExecBackgroundChildDoesNotHang(t *testing.T) {
	ts, _ := newTestServer(t)
	start := time.Now()
	out, _, exit := execCollect(t, ts, map[string]any{"cmd": "(sleep 20 &) ; echo done"})
	if exit["code"].(float64) != 0 || !strings.Contains(out, "done") {
		t.Fatalf("exit: %v out %q", exit, out)
	}
	if time.Since(start) > 4*time.Second {
		t.Fatal("background child blocked exec")
	}
}

func TestFS(t *testing.T) {
	ts, root := newTestServer(t)
	resp := do(t, ts, "POST", "/fs/write", map[string]any{"path": "/workspace/a/b/c.txt", "content": "hello ✓"})
	if resp.StatusCode != 200 {
		t.Fatalf("write %d", resp.StatusCode)
	}
	resp.Body.Close()
	b, _ := os.ReadFile(filepath.Join(root, "a/b/c.txt"))
	if string(b) != "hello ✓" {
		t.Fatalf("content %q", b)
	}
	resp = do(t, ts, "GET", "/fs/read?path=a/b/c.txt", nil)
	got, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(got) != "hello ✓" {
		t.Fatalf("read %q", got)
	}
	do(t, ts, "POST", "/fs/write", map[string]any{"path": "bin.dat", "content_b64": "AAEC", "mode": "0755"}).Body.Close()
	fi, _ := os.Stat(filepath.Join(root, "bin.dat"))
	if fi.Size() != 3 || fi.Mode().Perm() != 0o755 {
		t.Fatalf("bin %v %v", fi.Size(), fi.Mode())
	}

	st := jsonBody[map[string]any](t, do(t, ts, "GET", "/fs/stat?path=a/b/c.txt", nil))
	if st["exists"] != true || st["type"] != "file" {
		t.Fatalf("stat %v", st)
	}
	st = jsonBody[map[string]any](t, do(t, ts, "GET", "/fs/stat?path=nope", nil))
	if st["exists"] != false {
		t.Fatalf("stat %v", st)
	}

	os.MkdirAll(filepath.Join(root, "node_modules/x"), 0o755)
	list := jsonBody[struct{ Entries []fsEntry }](t, do(t, ts, "GET", "/fs/list?path=&depth=5", nil))
	paths := map[string]string{}
	for _, e := range list.Entries {
		paths[e.Path] = e.Type
	}
	if paths["a"] != "dir" || paths["a/b/c.txt"] != "file" || paths["node_modules"] != "dir" {
		t.Fatalf("list %v", paths)
	}
	if _, ok := paths["node_modules/x"]; ok {
		t.Fatal("node_modules should not be descended")
	}

	do(t, ts, "POST", "/fs/mv", map[string]any{"from": "a/b/c.txt", "to": "d/e.txt"}).Body.Close()
	if _, err := os.Stat(filepath.Join(root, "d/e.txt")); err != nil {
		t.Fatal("mv failed")
	}
	do(t, ts, "POST", "/fs/mkdir", map[string]any{"path": "x/y"}).Body.Close()
	do(t, ts, "POST", "/fs/rm", map[string]any{"path": "d"}).Body.Close()
	if _, err := os.Stat(filepath.Join(root, "d")); err == nil {
		t.Fatal("rm failed")
	}
	if fi, err := os.Stat(filepath.Join(root, "x/y")); err != nil || !fi.IsDir() {
		t.Fatal("mkdir failed")
	}
	resp = do(t, ts, "GET", "/fs/read?path=missing", nil)
	if resp.StatusCode != 404 {
		t.Fatalf("missing read %d", resp.StatusCode)
	}
	resp.Body.Close()
}

func TestFSEscape(t *testing.T) {
	ts, root := newTestServer(t)
	outside := t.TempDir()
	os.WriteFile(filepath.Join(outside, "secret"), []byte("s"), 0o644)
	os.Symlink(outside, filepath.Join(root, "link"))

	// ../ is clamped to root rather than escaping
	do(t, ts, "POST", "/fs/write", map[string]any{"path": "../../escape.txt", "content": "x"}).Body.Close()
	if _, err := os.Stat(filepath.Join(root, "escape.txt")); err != nil {
		t.Fatal("expected clamped write inside root")
	}
	resp := do(t, ts, "GET", "/fs/read?path=link/secret", nil)
	if resp.StatusCode != 400 {
		t.Fatalf("symlink escape not blocked: %d", resp.StatusCode)
	}
	resp.Body.Close()
	resp = do(t, ts, "POST", "/fs/rm", map[string]any{"path": "/"})
	if resp.StatusCode != 400 {
		t.Fatalf("rm root should be rejected: %d", resp.StatusCode)
	}
	resp.Body.Close()
}

func TestProcessSupervisor(t *testing.T) {
	ts, root := newTestServer(t)
	marker := filepath.Join(root, "count")
	cmd := "echo start-$NAME; echo x >> count; sleep 0.2; exit 1"
	p := jsonBody[map[string]any](t, do(t, ts, "POST", "/process/ensure", map[string]any{"name": "crashy", "cmd": cmd, "env": map[string]string{"NAME": "a"}}))
	if p["status"] != "running" || p["pid"].(float64) <= 0 {
		t.Fatalf("ensure %v", p)
	}
	// idempotent: same definition returns same process
	p2 := jsonBody[map[string]any](t, do(t, ts, "POST", "/process/ensure", map[string]any{"name": "crashy", "cmd": cmd, "env": map[string]string{"NAME": "a"}}))
	if p2["pid"] != p["pid"] && p2["restarts"].(float64) == 0 {
		t.Fatalf("ensure not idempotent: %v vs %v", p, p2)
	}
	time.Sleep(1800 * time.Millisecond) // first restart after 1s backoff
	b, _ := os.ReadFile(marker)
	if strings.Count(string(b), "x") < 2 {
		t.Fatalf("expected restart, count=%q", b)
	}
	logs := jsonBody[struct{ Lines []string }](t, do(t, ts, "GET", "/process/logs?name=crashy&tail=50", nil))
	joined := strings.Join(logs.Lines, "\n")
	if !strings.Contains(joined, "start-a") || !strings.Contains(joined, "restarting") {
		t.Fatalf("logs %q", joined)
	}
	list := jsonBody[[]map[string]any](t, do(t, ts, "GET", "/process/list", nil))
	if len(list) != 1 || list[0]["name"] != "crashy" {
		t.Fatalf("list %v", list)
	}
	do(t, ts, "POST", "/process/stop", map[string]any{"name": "crashy"}).Body.Close()
	list = jsonBody[[]map[string]any](t, do(t, ts, "GET", "/process/list", nil))
	if list[0]["status"] != "stopped" {
		t.Fatalf("not stopped: %v", list)
	}
}

func TestProcessLogsFollowAndPorts(t *testing.T) {
	ts, _ := newTestServer(t)
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	freePort := ln.Addr().(*net.TCPAddr).Port
	ln.Close()

	py := "import http.server,socketserver;print('serving',flush=True);socketserver.TCPServer(('127.0.0.1'," + strconv.Itoa(freePort) + "),http.server.SimpleHTTPRequestHandler).serve_forever()"
	do(t, ts, "POST", "/process/ensure", map[string]any{"name": "web", "cmd": "python3 -c \"" + py + "\""}).Body.Close()

	req, _ := http.NewRequest("GET", ts.URL+"/process/logs?name=web&follow=1&tail=10", nil)
	req.Header.Set("X-Sandbox-Token", tok)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	lines := make(chan string, 10)
	go func() {
		sc := bufio.NewScanner(resp.Body)
		for sc.Scan() {
			if strings.HasPrefix(sc.Text(), "data: ") {
				lines <- sc.Text()
			}
		}
	}()
	select {
	case l := <-lines:
		if !strings.Contains(l, "serving") {
			t.Fatalf("line %q", l)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("no streamed log")
	}
	resp.Body.Close()

	deadline := time.Now().Add(5 * time.Second)
	for {
		res := jsonBody[struct{ Open []int }](t, do(t, ts, "GET", "/ports?check=1,"+strconv.Itoa(freePort), nil))
		if len(res.Open) == 1 && res.Open[0] == freePort {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("port never open: %v", res)
		}
		time.Sleep(100 * time.Millisecond)
	}
	do(t, ts, "POST", "/process/stop", map[string]any{"name": "web"}).Body.Close()
}

func TestKernel(t *testing.T) {
	ts, _ := newTestServer(t)
	exec := func(code string, timeout int) kernelResult {
		return jsonBody[kernelResult](t, do(t, ts, "POST", "/kernel/execute", map[string]any{"code": code, "timeout_ms": timeout}))
	}
	r := exec("x = 21\nprint('hello')\nimport os; os.system('echo raw-fd-output')\nx * 2", 0)
	if !r.OK {
		t.Fatalf("not ok: %v", r)
	}
	text := ""
	for _, o := range r.Outputs {
		if o["type"] == "stream" {
			text += o["text"].(string)
		}
	}
	if !strings.Contains(text, "hello\n") || !strings.Contains(text, "42") {
		t.Fatalf("outputs %v", r.Outputs)
	}
	r = exec("print(x + 1)", 0) // state persists
	if r.Outputs[0]["text"] != "22\n" {
		t.Fatalf("persist %v", r.Outputs)
	}
	r = exec("1/0", 0)
	if r.OK || r.Outputs[len(r.Outputs)-1]["ename"] != "ZeroDivisionError" {
		t.Fatalf("error %v", r)
	}
	r = exec("import time; time.sleep(10)", 500)
	if r.OK || r.Outputs[0]["ename"] != "TimeoutError" {
		t.Fatalf("timeout %v", r)
	}
	r = exec("print('x' in dir())", 0) // restarted → fresh state
	if r.Outputs[0]["text"] != "False\n" {
		t.Fatalf("after restart %v", r.Outputs)
	}
	r = exec("import sys; sys.exit(0)", 0)
	if r.OK {
		t.Fatalf("sys.exit should report error: %v", r)
	}
	exec("y = 1", 0)
	resp := do(t, ts, "POST", "/kernel/restart", nil)
	resp.Body.Close()
	r = exec("print('y' in dir())", 0)
	if r.Outputs[0]["text"] != "False\n" {
		t.Fatalf("restart %v", r.Outputs)
	}
}
