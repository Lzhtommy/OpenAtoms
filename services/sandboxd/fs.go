package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"io/fs"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"
)

type fsEntry struct {
	Name  string `json:"name"`
	Path  string `json:"path"`
	Type  string `json:"type"`
	Size  int64  `json:"size"`
	Mtime string `json:"mtime"`
}

// Directories never descended into when depth > 1.
var skipDirs = map[string]bool{"node_modules": true, ".git": true, "dist": true, ".venv": true, "__pycache__": true}

func (s *Server) entry(full string, info fs.FileInfo) fsEntry {
	t := "file"
	if info.IsDir() {
		t = "dir"
	}
	return fsEntry{Name: info.Name(), Path: s.rel(full), Type: t, Size: info.Size(), Mtime: info.ModTime().UTC().Format(time.RFC3339)}
}

func (s *Server) handleFsList(w http.ResponseWriter, r *http.Request) {
	full, err := s.resolve(r.URL.Query().Get("path"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	depth := 1
	if d, err := strconv.Atoi(r.URL.Query().Get("depth")); err == nil && d > 0 {
		depth = min(d, 20)
	}
	info, err := os.Stat(full)
	if err != nil {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	if !info.IsDir() {
		writeErr(w, http.StatusBadRequest, "not a directory")
		return
	}
	entries := []fsEntry{}
	var walk func(dir string, level int)
	walk = func(dir string, level int) {
		items, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		sort.Slice(items, func(i, j int) bool {
			if items[i].IsDir() != items[j].IsDir() {
				return items[i].IsDir()
			}
			return items[i].Name() < items[j].Name()
		})
		for _, it := range items {
			p := filepath.Join(dir, it.Name())
			fi, err := it.Info()
			if err != nil {
				continue
			}
			entries = append(entries, s.entry(p, fi))
			if it.IsDir() && level < depth && !skipDirs[it.Name()] {
				walk(p, level+1)
			}
		}
	}
	walk(full, 1)
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries})
}

func (s *Server) handleFsRead(w http.ResponseWriter, r *http.Request) {
	full, err := s.resolve(r.URL.Query().Get("path"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	info, err := os.Stat(full)
	if err != nil || info.IsDir() {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	ct := mime.TypeByExtension(filepath.Ext(full))
	if ct == "" {
		ct = "application/octet-stream"
	}
	w.Header().Set("Content-Type", ct)
	http.ServeFile(w, r, full)
}

func (s *Server) handleFsStat(w http.ResponseWriter, r *http.Request) {
	full, err := s.resolve(r.URL.Query().Get("path"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	info, err := os.Stat(full)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"exists": false, "type": nil, "size": 0, "mtime": nil})
		return
	}
	e := s.entry(full, info)
	writeJSON(w, http.StatusOK, map[string]any{"exists": true, "type": e.Type, "size": e.Size, "mtime": e.Mtime})
}

type writeReq struct {
	Path       string          `json:"path"`
	Content    *string         `json:"content"`
	ContentB64 *string         `json:"content_b64"`
	Mode       json.RawMessage `json:"mode"`
}

func parseMode(raw json.RawMessage) (os.FileMode, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return 0o644, nil
	}
	var n int64
	if err := json.Unmarshal(raw, &n); err == nil {
		return os.FileMode(n), nil
	}
	var str string
	if err := json.Unmarshal(raw, &str); err != nil {
		return 0, errors.New("invalid mode")
	}
	v, err := strconv.ParseUint(str, 8, 32)
	if err != nil {
		return 0, errors.New("invalid mode")
	}
	return os.FileMode(v), nil
}

func (s *Server) handleFsWrite(w http.ResponseWriter, r *http.Request) {
	var req writeReq
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	full, err := s.resolve(req.Path)
	if err != nil || full == s.root {
		writeErr(w, http.StatusBadRequest, "invalid path")
		return
	}
	var data []byte
	switch {
	case req.ContentB64 != nil:
		data, err = base64.StdEncoding.DecodeString(*req.ContentB64)
		if err != nil {
			writeErr(w, http.StatusBadRequest, "invalid content_b64")
			return
		}
	case req.Content != nil:
		data = []byte(*req.Content)
	}
	mode, err := parseMode(req.Mode)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	// atomic replace
	tmp, err := os.CreateTemp(filepath.Dir(full), ".sandboxd-*")
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	_, werr := tmp.Write(data)
	cerr := tmp.Close()
	if werr != nil || cerr != nil {
		os.Remove(tmp.Name())
		writeErr(w, http.StatusInternalServerError, "write failed")
		return
	}
	_ = os.Chmod(tmp.Name(), mode)
	if err := os.Rename(tmp.Name(), full); err != nil {
		os.Remove(tmp.Name())
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "path": s.rel(full), "size": len(data)})
}

func (s *Server) handleFsMkdir(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Path string `json:"path"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	full, err := s.resolve(req.Path)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := os.MkdirAll(full, 0o755); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleFsRm(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Path string `json:"path"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	full, err := s.resolve(req.Path)
	if err != nil || full == s.root {
		writeErr(w, http.StatusBadRequest, "invalid path")
		return
	}
	if err := os.RemoveAll(full); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleFsMv(w http.ResponseWriter, r *http.Request) {
	var req struct {
		From string `json:"from"`
		To   string `json:"to"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	from, err1 := s.resolve(req.From)
	to, err2 := s.resolve(req.To)
	if err1 != nil || err2 != nil || from == s.root || to == s.root {
		writeErr(w, http.StatusBadRequest, "invalid path")
		return
	}
	if _, err := os.Stat(from); err != nil {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := os.Rename(from, to); err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}
