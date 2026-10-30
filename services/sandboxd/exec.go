package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"os/exec"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unicode/utf8"
)

const defaultExecTimeout = 600_000

type execReq struct {
	Cmd       string            `json:"cmd"`
	Cwd       string            `json:"cwd"`
	Env       map[string]string `json:"env"`
	TimeoutMs int               `json:"timeout_ms"`
}

func (s *Server) handleExec(w http.ResponseWriter, r *http.Request) {
	var req execReq
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if req.Cmd == "" {
		writeErr(w, http.StatusBadRequest, "cmd required")
		return
	}
	cwd, err := s.resolve(req.Cwd)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	timeout := req.TimeoutMs
	if timeout <= 0 {
		timeout = defaultExecTimeout
	}

	cmd := exec.Command("bash", "-c", req.Cmd)
	cmd.Dir = cwd
	cmd.Env = s.baseEnv(req.Env)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	outR, outW, _ := os.Pipe()
	errR, errW, _ := os.Pipe()
	cmd.Stdout, cmd.Stderr = outW, errW

	sse := newSSE(w)
	err = cmd.Start()
	outW.Close()
	errW.Close()
	if err != nil {
		outR.Close()
		errR.Close()
		_ = sse.send(map[string]any{"type": "stderr", "data": err.Error() + "\n"})
		_ = sse.send(map[string]any{"type": "exit", "code": 127, "timed_out": false})
		return
	}

	var wg sync.WaitGroup
	pump := func(rd *os.File, kind string) {
		defer wg.Done()
		streamChunks(rd, func(chunk string) { _ = sse.send(map[string]any{"type": kind, "data": chunk}) })
	}
	wg.Add(2)
	go pump(outR, "stdout")
	go pump(errR, "stderr")

	var timedOut atomic.Bool
	done := make(chan struct{})
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(timeout)*time.Millisecond)
	defer cancel()
	go func() {
		select {
		case <-ctx.Done():
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				timedOut.Store(true)
			}
			killGroup(cmd)
		case <-done:
		}
	}()

	err = cmd.Wait()
	close(done)
	// Background children may still hold the pipes open; drain briefly then detach.
	drained := make(chan struct{})
	go func() { wg.Wait(); close(drained) }()
	select {
	case <-drained:
	case <-time.After(500 * time.Millisecond):
		outR.Close()
		errR.Close()
		<-drained
	}
	outR.Close()
	errR.Close()
	code := exitCode(err)
	if timedOut.Load() {
		code = 124
	}
	_ = sse.send(map[string]any{"type": "exit", "code": code, "timed_out": timedOut.Load()})
}

// streamChunks reads rd and emits valid UTF-8 chunks (never splitting a rune).
func streamChunks(rd io.Reader, emit func(string)) {
	buf := make([]byte, 32*1024)
	var carry []byte
	for {
		n, err := rd.Read(buf)
		if n > 0 {
			data := append(carry, buf[:n]...)
			cut := len(data)
			// hold back an incomplete trailing rune
			for i := 0; i < utf8.UTFMax && cut > 0; i++ {
				if utf8.Valid(data[:cut]) {
					break
				}
				cut--
			}
			if cut == 0 { // invalid bytes, emit as-is (replaced)
				cut = len(data)
			}
			emit(string([]rune(string(data[:cut]))))
			carry = append([]byte(nil), data[cut:]...)
		}
		if err != nil {
			if len(carry) > 0 {
				emit(string(carry))
			}
			return
		}
	}
}

func killGroup(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
	go func(pid int) {
		time.Sleep(2 * time.Second)
		_ = syscall.Kill(-pid, syscall.SIGKILL)
	}(cmd.Process.Pid)
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		if ws, ok := ee.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			return 128 + int(ws.Signal())
		}
		return ee.ExitCode()
	}
	return 1
}
