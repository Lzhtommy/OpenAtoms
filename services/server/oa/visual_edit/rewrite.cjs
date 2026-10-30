#!/usr/bin/env node
/*
 * OpenAtoms visual-edit source rewriter.
 *
 * stdin : {"filename": "src/App.tsx", "source": "...", "edits": [{"line": 12, "col": 5, "add": ["text-lg"],
 *          "remove": ["text-sm"], "text": "New title" | null}]}
 * stdout: {"source": "...", "results": [{"ok": true} | {"ok": false, "error": "..."}]}
 *
 * line/col are 1-based and point at the `<` of a JSX opening/self-closing element (as stamped by the
 * template's data-oa-loc Babel plugin). Parsing uses the project's own `typescript` compiler API (resolved
 * through NODE_PATH), so TSX is handled exactly; all edits are applied as non-overlapping text splices.
 */
"use strict";
const ts = require("typescript");

const CLASS_HELPERS = new Set(["cn", "clsx", "classNames", "classnames", "twMerge", "twJoin"]);

function readStdin() {
  return new Promise((resolve) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (buf += c));
    process.stdin.on("end", () => resolve(buf));
  });
}

function mergeTokens(text, add, remove) {
  const out = text.split(/\s+/).filter(Boolean).filter((t) => !remove.includes(t));
  for (const a of add) if (!out.includes(a)) out.push(a);
  return out.join(" ");
}

function quoteLike(lit, value) {
  // keep the literal's own quote style; escape only what that style needs
  const q = lit.getText()[0];
  if (q === "`") return "`" + value.replace(/[`\\]/g, "\\$&").replace(/\$\{/g, "\\${") + "`";
  return q + value.replace(new RegExp(`[${q}\\\\]`, "g"), "\\$&") + q;
}

function jsxTextValue(value) {
  return /[{}<>]/.test(value) ? `{${JSON.stringify(value)}}` : value;
}

function rewrite({ filename, source, edits }) {
  const sf = ts.createSourceFile(filename || "file.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const byLoc = new Map();
  (function visit(n) {
    if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) {
      const p = sf.getLineAndCharacterOfPosition(n.getStart(sf));
      byLoc.set(`${p.line + 1}:${p.character + 1}`, n);
    }
    ts.forEachChild(n, visit);
  })(sf);

  const splices = []; // {start, end, text}
  const results = edits.map((e) => {
    const node = byLoc.get(`${e.line}:${e.col}`);
    if (!node) return { ok: false, error: `No JSX element at ${e.line}:${e.col} (the file changed since the preview loaded; reload and retry)` };
    const mine = [];
    const add = (e.add || []).filter(Boolean);
    const remove = (e.remove || []).filter(Boolean);
    if (add.length || remove.length) {
      const err = classEdit(sf, node, add, remove, mine);
      if (err) return { ok: false, error: err };
    }
    if (e.text !== undefined && e.text !== null) {
      const err = textEdit(sf, node, String(e.text), mine);
      if (err) return { ok: false, error: err };
    }
    for (const s of mine) {
      if (splices.some((o) => s.start < o.end && o.start < s.end)) return { ok: false, error: "Overlapping edits on the same element" };
    }
    splices.push(...mine);
    return { ok: true };
  });

  splices.sort((a, b) => b.start - a.start || b.end - a.end);
  let out = source;
  for (const s of splices) out = out.slice(0, s.start) + s.text + out.slice(s.end);
  return { source: out, results };
}

function classEdit(sf, node, add, remove, splices) {
  const attr = node.attributes.properties.find(
    (a) => ts.isJsxAttribute(a) && a.name.getText(sf) === "className"
  );
  if (!attr) {
    if (!add.length) return null;
    splices.push({ start: node.tagName.end, end: node.tagName.end, text: ` className="${add.join(" ")}"` });
    return null;
  }
  const init = attr.initializer;
  if (!init) return "className has no value";
  if (ts.isStringLiteral(init)) {
    splices.push({ start: init.getStart(sf), end: init.end, text: quoteLike(init, mergeTokens(init.text, add, remove)) });
    return null;
  }
  if (!ts.isJsxExpression(init) || !init.expression) return "className is computed dynamically; ask the AI to change this element";
  const ex = init.expression;
  if (ts.isStringLiteral(ex) || ts.isNoSubstitutionTemplateLiteral(ex)) {
    splices.push({ start: ex.getStart(sf), end: ex.end, text: quoteLike(ex, mergeTokens(ex.text, add, remove)) });
    return null;
  }
  if (ts.isTemplateExpression(ex)) {
    return "className is a template literal with expressions; ask the AI to change this element";
  }
  if (ts.isCallExpression(ex) && ts.isIdentifier(ex.expression) && CLASS_HELPERS.has(ex.expression.text)) {
    for (const arg of ex.arguments) {
      if ((ts.isStringLiteral(arg) || ts.isNoSubstitutionTemplateLiteral(arg)) && remove.some((r) => arg.text.split(/\s+/).includes(r))) {
        splices.push({ start: arg.getStart(sf), end: arg.end, text: quoteLike(arg, mergeTokens(arg.text, [], remove)) });
      }
    }
    if (add.length) {
      // later arguments win in cn()/twMerge, so new classes go last
      const at = ex.arguments.length ? ex.arguments[ex.arguments.length - 1].end : ex.arguments.pos;
      splices.push({ start: at, end: at, text: `${ex.arguments.length ? ", " : ""}"${add.join(" ")}"` });
    }
    return null;
  }
  return "className is computed dynamically; ask the AI to change this element";
}

function textEdit(sf, node, value, splices) {
  if (ts.isJsxSelfClosingElement(node)) return "This element has no text content";
  const el = node.parent;
  const kids = el.children.filter((c) => !(ts.isJsxText(c) && c.containsOnlyTriviaWhiteSpaces));
  if (kids.length === 0) {
    splices.push({ start: node.end, end: node.end, text: jsxTextValue(value) });
    return null;
  }
  if (kids.length === 1 && ts.isJsxText(kids[0])) {
    const raw = kids[0].getFullText(sf);
    const lead = raw.match(/^\s*/)[0].length;
    const trail = raw.match(/\s*$/)[0].length;
    const start = kids[0].getFullStart() + lead;
    splices.push({ start, end: kids[0].end - trail, text: jsxTextValue(value) });
    return null;
  }
  if (kids.length === 1 && ts.isJsxExpression(kids[0]) && kids[0].expression &&
      (ts.isStringLiteral(kids[0].expression) || ts.isNoSubstitutionTemplateLiteral(kids[0].expression))) {
    const lit = kids[0].expression;
    splices.push({ start: lit.getStart(sf), end: lit.end, text: quoteLike(lit, value) });
    return null;
  }
  return "The element's text mixes nested elements or expressions; ask the AI to change it";
}

readStdin()
  .then((raw) => {
    process.stdout.write(JSON.stringify(rewrite(JSON.parse(raw))));
  })
  .catch((err) => {
    process.stderr.write(String(err && err.stack ? err.stack : err));
    process.exit(1);
  });
