package main

import (
	"fmt"
	"net/http"
)

type page struct {
	title, heading, body string
	refresh              bool
}

var (
	pageWaking      = page{"Starting…", "Waking up your app", "The sandbox is starting. This page refreshes automatically.", true}
	pageForbidden   = page{"Private preview", "This preview is private", "Open it from the OpenAtoms workbench, or ask the owner to share it.", false}
	pageNotFound    = page{"Not found", "Nothing here", "This app does not exist or has not been published yet.", false}
	pageUnavailable = page{"Unavailable", "Temporarily unavailable", "The app could not be reached. Please try again in a moment.", true}
)

const pageTpl = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
%s<title>%s</title>
<style>
:root{color-scheme:light dark;--bg:#ebebea;--card:#fff;--fg:#0c0c0c;--muted:rgba(12,12,12,.55);--brand:#4267ff}
@media (prefers-color-scheme:dark){:root{--bg:#0f0f0f;--card:#1b1b1c;--fg:#f5f5f5;--muted:rgba(255,255,255,.6)}}
html,body{height:100%%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--fg);font:14px/1.5 "IBM Plex Sans",system-ui,-apple-system,sans-serif;padding:16px;box-sizing:border-box}
.card{background:var(--card);border-radius:16px;padding:32px 28px;max-width:420px;width:100%%;text-align:center;box-shadow:0 1px 2px rgba(0,0,0,.04)}
h1{font:600 20px/1.3 "IBM Plex Serif",Georgia,serif;margin:0 0 8px}
p{margin:0;color:var(--muted)}
.spin{width:28px;height:28px;margin:0 auto 18px;border:3px solid rgba(66,103,255,.2);border-top-color:var(--brand);border-radius:50%%;animation:s 0.9s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}
</style></head>
<body><div class="card">%s<h1>%s</h1><p>%s</p></div></body></html>`

func writePage(w http.ResponseWriter, code int, p page) {
	meta, spinner := "", ""
	if p.refresh {
		meta = `<meta http-equiv="refresh" content="2">`
		spinner = `<div class="spin" aria-hidden="true"></div>`
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_, _ = fmt.Fprintf(w, pageTpl, meta, p.title, spinner, p.heading, p.body)
}
