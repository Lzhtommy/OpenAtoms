package main

import (
	"bytes"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
)

const maxInjectSize = 20 << 20

func isHTML(h http.Header) bool {
	return strings.HasPrefix(strings.ToLower(strings.TrimSpace(h.Get("Content-Type"))), "text/html")
}

// injectHTML inserts the bridge script before the last </body> (or appends it).
func injectHTML(doc []byte) []byte {
	if bytes.Contains(doc, []byte("/__oa/bridge.js")) {
		return doc
	}
	lower := bytes.ToLower(doc)
	if i := bytes.LastIndex(lower, []byte("</body>")); i >= 0 {
		out := make([]byte, 0, len(doc)+len(bridgeScript))
		out = append(out, doc[:i]...)
		out = append(out, bridgeScript...)
		return append(out, doc[i:]...)
	}
	return append(doc, bridgeScript...)
}

// injectResponse rewrites an HTML response body in place: decodes gzip, injects
// the bridge, and fixes headers (length, CSP, framing).
func injectResponse(resp *http.Response, csp string) error {
	var rd io.Reader = resp.Body
	enc := strings.ToLower(resp.Header.Get("Content-Encoding"))
	switch enc {
	case "", "identity":
	case "gzip":
		zr, err := gzip.NewReader(resp.Body)
		if err != nil {
			return fmt.Errorf("gzip: %w", err)
		}
		defer zr.Close()
		rd = zr
	default:
		// Unknown encoding: pass through untouched rather than corrupt it.
		setFrameHeaders(resp.Header, csp)
		return nil
	}
	body, err := io.ReadAll(io.LimitReader(rd, maxInjectSize+1))
	resp.Body.Close()
	if err != nil {
		return err
	}
	if len(body) <= maxInjectSize {
		body = injectHTML(body)
	}
	resp.Body = io.NopCloser(bytes.NewReader(body))
	resp.ContentLength = int64(len(body))
	resp.Header.Del("Content-Encoding")
	resp.Header.Set("Content-Length", strconv.Itoa(len(body)))
	resp.Header.Del("ETag") // body changed
	setFrameHeaders(resp.Header, csp)
	return nil
}

func setFrameHeaders(h http.Header, csp string) {
	h.Del("Content-Security-Policy")
	h.Del("X-Frame-Options")
	h.Set("Content-Security-Policy", csp)
}
