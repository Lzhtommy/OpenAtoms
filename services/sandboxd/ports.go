package main

import (
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

func (s *Server) handlePorts(w http.ResponseWriter, r *http.Request) {
	var ports []int
	for _, part := range strings.Split(r.URL.Query().Get("check"), ",") {
		if p, err := strconv.Atoi(strings.TrimSpace(part)); err == nil && p > 0 && p < 65536 {
			ports = append(ports, p)
		}
	}
	open := make([]bool, len(ports))
	var wg sync.WaitGroup
	for i, p := range ports {
		wg.Add(1)
		go func(i, p int) {
			defer wg.Done()
			open[i] = probe(p)
		}(i, p)
	}
	wg.Wait()
	out := []int{}
	for i, p := range ports {
		if open[i] {
			out = append(out, p)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"open": out})
}

func probe(port int) bool {
	for _, host := range []string{"127.0.0.1", "::1"} {
		c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), 400*time.Millisecond)
		if err == nil {
			c.Close()
			return true
		}
	}
	return false
}
