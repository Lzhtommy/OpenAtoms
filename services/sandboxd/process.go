package main

import (
	"bufio"
	"io"
	"net/http"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"sync"
	"syscall"
	"time"
)

const logRingSize = 2000

// ring is a fixed-size line buffer with live subscribers.
type ring struct {
	mu    sync.Mutex
	lines []string
	start int
	n     int
	subs  map[chan string]struct{}
}

func newRing() *ring {
	return &ring{lines: make([]string, logRingSize), subs: map[chan string]struct{}{}}
}

func (r *ring) add(line string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	idx := (r.start + r.n) % logRingSize
	r.lines[idx] = line
	if r.n < logRingSize {
		r.n++
	} else {
		r.start = (r.start + 1) % logRingSize
	}
	for ch := range r.subs {
		select {
		case ch <- line:
		default: // slow subscriber: drop
		}
	}
}

func (r *ring) tail(n int) []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	if n <= 0 || n > r.n {
		n = r.n
	}
	out := make([]string, 0, n)
	for i := r.n - n; i < r.n; i++ {
		out = append(out, r.lines[(r.start+i)%logRingSize])
	}
	return out
}

func (r *ring) subscribe() (chan string, []string) {
	ch := make(chan string, 512)
	r.mu.Lock()
	r.subs[ch] = struct{}{}
	r.mu.Unlock()
	return ch, nil
}

func (r *ring) unsubscribe(ch chan string) {
	r.mu.Lock()
	delete(r.subs, ch)
	r.mu.Unlock()
}

// proc is one supervised process.
type proc struct {
	Name     string
	Cmd      string
	Cwd      string
	Env      map[string]string
	mu       sync.Mutex
	cmd      *exec.Cmd
	pid      int
	status   string // starting|running|backoff|stopped|exited
	restarts int
	stopped  bool
	stopCh   chan struct{}
	logs     *ring
}

func (p *proc) snapshot() map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	return map[string]any{"name": p.Name, "pid": p.pid, "status": p.status, "restarts": p.restarts, "cmd": p.Cmd}
}

// Supervisor runs named long-lived processes, restarting them on crash with backoff.
type Supervisor struct {
	s     *Server
	mu    sync.Mutex
	procs map[string]*proc
}

func NewSupervisor(s *Server) *Supervisor {
	return &Supervisor{s: s, procs: map[string]*proc{}}
}

func sameEnv(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

// Ensure starts the process if absent, or restarts it if its definition changed.
func (sv *Supervisor) Ensure(name, cmd, cwd string, env map[string]string) *proc {
	sv.mu.Lock()
	if p, ok := sv.procs[name]; ok {
		p.mu.Lock()
		same := p.Cmd == cmd && p.Cwd == cwd && sameEnv(p.Env, env) && !p.stopped
		p.mu.Unlock()
		if same {
			sv.mu.Unlock()
			return p
		}
		sv.mu.Unlock()
		sv.Stop(name)
		sv.mu.Lock()
	}
	logs := newRing()
	if old, ok := sv.procs[name]; ok {
		logs = old.logs // keep history across redefinition
	}
	p := &proc{Name: name, Cmd: cmd, Cwd: cwd, Env: env, status: "starting", stopCh: make(chan struct{}), logs: logs}
	sv.procs[name] = p
	sv.mu.Unlock()

	started := make(chan struct{})
	go sv.loop(p, started)
	<-started
	return p
}

func (sv *Supervisor) loop(p *proc, started chan struct{}) {
	backoff := time.Second
	first := true
	for {
		begin := time.Now()
		err := sv.startOnce(p)
		if first {
			close(started)
			first = false
		}
		if err == nil {
			_ = p.cmd.Wait()
		} else {
			p.logs.add("[sandboxd] failed to start: " + err.Error())
		}
		p.mu.Lock()
		if p.stopped {
			p.status = "stopped"
			p.pid = 0
			p.mu.Unlock()
			return
		}
		code := -1
		if err == nil && p.cmd.ProcessState != nil {
			code = p.cmd.ProcessState.ExitCode()
		}
		p.status = "backoff"
		p.pid = 0
		p.restarts++
		p.mu.Unlock()
		if time.Since(begin) > 30*time.Second {
			backoff = time.Second
		}
		p.logs.add("[sandboxd] process exited (code " + strconv.Itoa(code) + "), restarting in " + backoff.String())
		select {
		case <-p.stopCh:
			p.mu.Lock()
			p.status = "stopped"
			p.mu.Unlock()
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, 30*time.Second)
	}
}

func (sv *Supervisor) startOnce(p *proc) error {
	cwd, err := sv.s.resolve(p.Cwd)
	if err != nil {
		return err
	}
	c := exec.Command("bash", "-c", p.Cmd)
	c.Dir = cwd
	c.Env = sv.s.baseEnv(p.Env)
	c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	pr, pw, err := os.Pipe()
	if err != nil {
		return err
	}
	c.Stdout, c.Stderr = pw, pw
	if err := c.Start(); err != nil {
		pr.Close()
		pw.Close()
		return err
	}
	pw.Close()
	go scanLines(pr, p.logs.add)
	p.mu.Lock()
	p.cmd = c
	p.pid = c.Process.Pid
	p.status = "running"
	p.mu.Unlock()
	return nil
}

func scanLines(rd io.ReadCloser, emit func(string)) {
	defer rd.Close()
	sc := bufio.NewScanner(rd)
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	for sc.Scan() {
		emit(sc.Text())
	}
}

func (sv *Supervisor) Stop(name string) bool {
	sv.mu.Lock()
	p, ok := sv.procs[name]
	sv.mu.Unlock()
	if !ok {
		return false
	}
	p.mu.Lock()
	if !p.stopped {
		p.stopped = true
		close(p.stopCh)
	}
	pid := p.pid
	p.mu.Unlock()
	if pid > 0 {
		_ = syscall.Kill(-pid, syscall.SIGTERM)
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			if syscall.Kill(-pid, 0) != nil {
				break
			}
			time.Sleep(50 * time.Millisecond)
		}
		_ = syscall.Kill(-pid, syscall.SIGKILL)
	}
	// wait for loop to observe stop
	for i := 0; i < 100; i++ {
		p.mu.Lock()
		st := p.status
		p.mu.Unlock()
		if st == "stopped" {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	return true
}

func (sv *Supervisor) StopAll() {
	sv.mu.Lock()
	names := make([]string, 0, len(sv.procs))
	for n := range sv.procs {
		names = append(names, n)
	}
	sv.mu.Unlock()
	for _, n := range names {
		sv.Stop(n)
	}
}

func (sv *Supervisor) get(name string) *proc {
	sv.mu.Lock()
	defer sv.mu.Unlock()
	return sv.procs[name]
}

// ---- handlers

func (s *Server) handleProcEnsure(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name string            `json:"name"`
		Cmd  string            `json:"cmd"`
		Cwd  string            `json:"cwd"`
		Env  map[string]string `json:"env"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if req.Name == "" || req.Cmd == "" {
		writeErr(w, http.StatusBadRequest, "name and cmd required")
		return
	}
	if _, err := s.resolve(req.Cwd); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	p := s.procs.Ensure(req.Name, req.Cmd, req.Cwd, req.Env)
	writeJSON(w, http.StatusOK, p.snapshot())
}

func (s *Server) handleProcStop(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name string `json:"name"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if !s.procs.Stop(req.Name) {
		writeErr(w, http.StatusNotFound, "no such process")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleProcList(w http.ResponseWriter, _ *http.Request) {
	s.procs.mu.Lock()
	ps := make([]*proc, 0, len(s.procs.procs))
	for _, p := range s.procs.procs {
		ps = append(ps, p)
	}
	s.procs.mu.Unlock()
	sort.Slice(ps, func(i, j int) bool { return ps[i].Name < ps[j].Name })
	out := make([]map[string]any, 0, len(ps))
	for _, p := range ps {
		out = append(out, p.snapshot())
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleProcLogs(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	p := s.procs.get(q.Get("name"))
	if p == nil {
		writeErr(w, http.StatusNotFound, "no such process")
		return
	}
	tail := 200
	if t, err := strconv.Atoi(q.Get("tail")); err == nil && t >= 0 {
		tail = t
	}
	if q.Get("follow") != "1" {
		lines := []string{}
		if tail > 0 {
			lines = append(lines, p.logs.tail(tail)...)
		}
		writeJSON(w, http.StatusOK, map[string]any{"lines": lines})
		return
	}
	ch, _ := p.logs.subscribe()
	defer p.logs.unsubscribe(ch)
	sse := newSSE(w)
	if tail > 0 {
		for _, l := range p.logs.tail(tail) {
			if sse.send(map[string]string{"line": l}) != nil {
				return
			}
		}
	}
	ping := time.NewTicker(15 * time.Second)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case l := <-ch:
			if sse.send(map[string]string{"line": l}) != nil {
				return
			}
		case <-ping.C:
			sse.mu.Lock()
			_, err := w.Write([]byte(": ping\n\n"))
			sse.flush()
			sse.mu.Unlock()
			if err != nil {
				return
			}
		}
	}
}
