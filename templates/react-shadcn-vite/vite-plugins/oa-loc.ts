/**
 * OpenAtoms visual-edit source mapping (dev server only).
 *
 * A Babel plugin, run through @vitejs/plugin-react's Babel pipeline, that stamps JSX elements with
 * `data-oa-loc="src/App.tsx:12:5"` (file relative to the project root, 1-based line and column of the `<`).
 * The workbench reads it from the selected DOM node and the server rewrites that exact JSX tag.
 *
 * The attribute is inserted FIRST so that a component which spreads its props onto a host element
 * (`<button {...props}>`) lets the call-site location override the component's internal one — editing
 * `<Button className=…>` in App.tsx then targets App.tsx, not components/ui/button.tsx.
 * Fragments are skipped (React warns on unknown Fragment props). Production builds never load this.
 */
import path from "node:path";

const ATTR = "data-oa-loc";

// Minimal structural types for the parts of the Babel API we use (avoids a @babel/core types dependency).
type Node = { type: string; [k: string]: unknown };
type T = {
  jsxAttribute(name: Node, value: Node): Node;
  jsxIdentifier(name: string): Node;
  stringLiteral(value: string): Node;
};
type OpeningPath = {
  node: {
    name: Node & { name?: string; property?: { name?: string } };
    attributes: Array<Node & { name?: { name?: string } }>;
    loc?: { start: { line: number; column: number } } | null;
  };
};

function isFragment(name: OpeningPath["node"]["name"]): boolean {
  if (name.type === "JSXIdentifier") return name.name === "Fragment";
  if (name.type === "JSXMemberExpression") return name.property?.name === "Fragment";
  return false;
}

export function oaLocBabelPlugin(root: string) {
  return ({ types: t }: { types: T }) => ({
    name: "oa-loc",
    visitor: {
      JSXOpeningElement(p: OpeningPath, state: { filename?: string }) {
        const { node } = p;
        const file = state.filename;
        if (!file || !node.loc || file.includes("node_modules")) return;
        if (isFragment(node.name)) return;
        if (node.attributes.some((a) => a.type === "JSXAttribute" && a.name?.name === ATTR)) return;
        const rel = path.relative(root, file).split(path.sep).join("/");
        const loc = `${rel}:${node.loc.start.line}:${node.loc.start.column + 1}`;
        node.attributes.unshift(t.jsxAttribute(t.jsxIdentifier(ATTR), t.stringLiteral(loc)));
      },
    },
  });
}
