package main

import (
	"bufio"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

//go:embed kernel_driver.py
var kernelDriver []byte

// Kernel is a persistent Python REPL. Calls are serialized.
type Kernel struct {
	root    string
	envFn   func(map[string]string) []string
	mu      sync.Mutex
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	replies *bufio.Reader
	protoR  *os.File
	nextID  int
	dead    chan struct{}
	logs    *ring // raw stdout/stderr written by user code at fd level
}

func NewKernel(root string, envFn func(map[string]string) []string) *Kernel {
	return &Kernel{root: root, envFn: envFn, logs: newRing()}
}

func pythonBin() string {
	if p := os.Getenv("SANDBOXD_PYTHON"); p != "" {
		return p
	}
	return "python3"
}

func (k *Kernel) start() error {
	dir, err := os.MkdirTemp("", "sandboxd-kernel-")
	if err != nil {
		return err
	}
	script := filepath.Join(dir, "driver.py")
	if err := os.WriteFile(script, kernelDriver, 0o644); err != nil {
		return err
	}
	pr, pw, err := os.Pipe()
	if err != nil {
		return err
	}
	c := exec.Command(pythonBin(), "-u", script)
	c.Dir = k.root
	c.Env = k.envFn(map[string]string{"MPLBACKEND": "Agg", "PYTHONUNBUFFERED": "1"})
	c.ExtraFiles = []*os.File{pw} // fd 3
	c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	stdin, err := c.StdinPipe()
	if err != nil {
		return err
	}
	outR, outW, _ := os.Pipe()
	c.Stdout, c.Stderr = outW, outW
	if err := c.Start(); err != nil {
		pr.Close()
		pw.Close()
		outR.Close()
		outW.Close()
		return err
	}
	pw.Close()
	outW.Close()
	go scanLines(outR, k.logs.add)
	dead := make(chan struct{})
	go func() {
		_ = c.Wait()
		os.RemoveAll(dir)
		close(dead)
	}()
	k.cmd, k.stdin, k.protoR, k.replies, k.dead = c, stdin, pr, bufio.NewReaderSize(pr, 1<<20), dead
	return nil
}

func (k *Kernel) stopLocked() {
	if k.cmd == nil {
		return
	}
	_ = k.stdin.Close()
	_ = syscall.Kill(-k.cmd.Process.Pid, syscall.SIGKILL)
	select {
	case <-k.dead:
	case <-time.After(3 * time.Second):
	}
	k.protoR.Close()
	k.cmd = nil
}

func (k *Kernel) alive() bool {
	if k.cmd == nil {
		return false
	}
	select {
	case <-k.dead:
		return false
	default:
		return true
	}
}

type kernelResult struct {
	OK      bool             `json:"ok"`
	Outputs []map[string]any `json:"outputs"`
}

var errKernelTimeout = errors.New("kernel execution timed out")

// Execute runs code in the persistent namespace. On timeout the kernel is restarted.
func (k *Kernel) Execute(code string, timeout time.Duration) (*kernelResult, error) {
	k.mu.Lock()
	defer k.mu.Unlock()
	if !k.alive() {
		if k.cmd != nil {
			k.stopLocked()
		}
		if err := k.start(); err != nil {
			return nil, fmt.Errorf("start kernel: %w", err)
		}
	}
	k.nextID++
	id := k.nextID
	req, _ := json.Marshal(map[string]any{"id": id, "code": code})
	if _, err := k.stdin.Write(append(req, '\n')); err != nil {
		k.stopLocked()
		return nil, fmt.Errorf("kernel write: %w", err)
	}

	type reply struct {
		res *kernelResult
		err error
	}
	ch := make(chan reply, 1)
	replies := k.replies
	go func() {
		for {
			line, err := replies.ReadBytes('\n')
			if err != nil {
				ch <- reply{err: errors.New("kernel died")}
				return
			}
			var msg struct {
				ID int `json:"id"`
				kernelResult
			}
			if json.Unmarshal(line, &msg) != nil || msg.ID != id {
				continue
			}
			r := msg.kernelResult
			ch <- reply{res: &r}
			return
		}
	}()
	select {
	case rep := <-ch:
		if rep.err != nil {
			k.stopLocked()
			return &kernelResult{OK: false, Outputs: []map[string]any{{
				"type": "error", "ename": "KernelDied", "evalue": "the Python kernel exited; state was reset", "traceback": "",
			}}}, nil
		}
		if rep.res.Outputs == nil {
			rep.res.Outputs = []map[string]any{}
		}
		return rep.res, nil
	case <-time.After(timeout):
		k.stopLocked()
		return &kernelResult{OK: false, Outputs: []map[string]any{{
			"type": "error", "ename": "TimeoutError", "evalue": errKernelTimeout.Error() + "; kernel restarted", "traceback": "",
		}}}, nil
	}
}

func (k *Kernel) Restart() error {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.stopLocked()
	return k.start()
}

func (k *Kernel) Close() {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.stopLocked()
}

func (s *Server) handleKernelExec(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Code      string `json:"code"`
		TimeoutMs int    `json:"timeout_ms"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	timeout := 120 * time.Second
	if req.TimeoutMs > 0 {
		timeout = time.Duration(req.TimeoutMs) * time.Millisecond
	}
	res, err := s.kernel.Execute(req.Code, timeout)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, res)
}

func (s *Server) handleKernelRestart(w http.ResponseWriter, _ *http.Request) {
	if err := s.kernel.Restart(); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}
