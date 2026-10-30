"""Source code the `oa-mock` Engineer writes. Each app is self-contained, strictly typed and styled with
Tailwind so that `pnpm build` passes on the react-shadcn-vite template. `{{ACCENT}}` is replaced with a
Tailwind color name (blue/emerald/violet/...) so class names stay literal for the JIT compiler.
"""
from __future__ import annotations

import json
import re

KINDS = ("todo", "pomodoro", "counter", "landing")

TITLES = {
    "todo": ("Todo List", "待办清单"),
    "pomodoro": ("Pomodoro Timer", "番茄钟"),
    "counter": ("Counter", "计数器"),
    "landing": ("", ""),
}

COMPONENT = {"todo": "TodoApp", "pomodoro": "Pomodoro", "counter": "Counter", "landing": "Landing"}


def detect_kind(req: str) -> str:
    r = req.lower()
    if re.search(r"todo|to-do|task list|checklist|待办|任务清单|清单", r):
        return "todo"
    if re.search(r"pomodoro|timer|countdown|focus|番茄|计时|倒计时", r):
        return "pomodoro"
    if re.search(r"counter|count|tally|计数", r):
        return "counter"
    return "landing"


def wants_login(req: str) -> bool:
    return bool(re.search(r"login|log in|sign in|auth|account|登录|登陆|注册|账号", req.lower()))


def has_cjk(s: str) -> bool:
    return bool(re.search(r"[一-鿿]", s))


def landing_title(req: str) -> str:
    s = re.sub(r"[{}<>`\"'\\]", "", req).strip()
    s = re.sub(r"^(please\s+)?(build|make|create|design|generate|做|帮我做|请做|制作|生成|创建)\s*(an?|一个|个)?\s*", "", s, flags=re.IGNORECASE)
    s = s.split("\n")[0][:48].strip(" .,。，")
    return s[:1].upper() + s[1:] if s else "Your Next Big Idea"


def app_title(kind: str, req: str) -> str:
    en, zh = TITLES[kind]
    if kind == "landing":
        return landing_title(req)
    return zh if has_cjk(req) else en


def app_tsx(kind: str, req: str, draft: bool = True) -> str:
    comp = COMPONENT[kind]
    title = json.dumps(app_title(kind, req), ensure_ascii=False)
    subtitle = "Draft" if draft else subtitle_for(kind, req)
    return f'''import {comp} from "./components/{comp}";

export default function App() {{
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 to-slate-100 text-slate-900">
      <header className="mx-auto max-w-3xl px-6 pb-6 pt-12">
        <h1 className="text-3xl font-bold tracking-tight">{{{title}}}</h1>
        <p className="mt-2 text-slate-500">{subtitle}</p>
      </header>
      <main className="mx-auto max-w-3xl px-6 pb-16">
        <{comp} />
      </main>
      <footer className="pb-8 text-center text-xs text-slate-400">Built by Alex with OpenAtoms</footer>
    </div>
  );
}}
'''


def subtitle_for(kind: str, req: str) -> str:
    zh = has_cjk(req)
    return {
        "todo": "管理你的每日任务，数据保存在本地浏览器。" if zh else "Plan your day. Your tasks are saved in this browser.",
        "pomodoro": "专注 25 分钟，休息 5 分钟。" if zh else "Focus for 25 minutes, then take a short break.",
        "counter": "简单可靠的计数工具，支持步长与撤销。" if zh else "A simple, reliable tally with steps and undo.",
        "landing": "为你的想法打造的落地页。" if zh else "A landing page crafted for your idea.",
    }[kind]


def component_tsx(kind: str, req: str, accent: str = "blue") -> str:
    src = {
        "todo": TODO_LOGIN if wants_login(req) else TODO,
        "pomodoro": POMODORO,
        "counter": COUNTER,
        "landing": LANDING.replace("{{TITLE}}", json.dumps(landing_title(req), ensure_ascii=False)),
    }[kind]
    return src.replace("{{ACCENT}}", accent)


_TODO_CORE = r'''
type Todo = { id: string; text: string; done: boolean; createdAt: number };
type Filter = "all" | "active" | "done";

function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function loadTodos(key: string): Todo[] {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as Todo[]) : [];
  } catch {
    return [];
  }
}

function TodoBoard({ storageKey }: { storageKey: string }) {
  const [todos, setTodos] = useState<Todo[]>(() => loadTodos(storageKey));
  const [text, setText] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify(todos));
  }, [todos, storageKey]);

  const visible = useMemo(
    () => todos.filter((t) => (filter === "all" ? true : filter === "done" ? t.done : !t.done)),
    [todos, filter],
  );
  const remaining = todos.filter((t) => !t.done).length;

  function add(e: FormEvent) {
    e.preventDefault();
    const value = text.trim();
    if (!value) return;
    setTodos((ts) => [{ id: uid(), text: value, done: false, createdAt: Date.now() }, ...ts]);
    setText("");
  }

  const toggle = (id: string) => setTodos((ts) => ts.map((t) => (t.id === id ? { ...t, done: !t.done } : t)));
  const remove = (id: string) => setTodos((ts) => ts.filter((t) => t.id !== id));
  const clearDone = () => setTodos((ts) => ts.filter((t) => !t.done));

  return (
    <div className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
      <form onSubmit={add} className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="What needs to be done?"
          className="flex-1 rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-{{ACCENT}}-500 focus:ring-2 focus:ring-{{ACCENT}}-100"
        />
        <button
          type="submit"
          className="inline-flex items-center gap-1 rounded-lg bg-{{ACCENT}}-600 px-4 py-2 font-medium text-white hover:bg-{{ACCENT}}-700"
        >
          <Plus className="h-4 w-4" /> Add
        </button>
      </form>

      <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
        <span>{remaining} left</span>
        <div className="flex gap-1">
          {(["all", "active", "done"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={
                "rounded-md px-2 py-1 capitalize " +
                (filter === f ? "bg-{{ACCENT}}-50 text-{{ACCENT}}-700" : "hover:bg-slate-100")
              }
            >
              {f}
            </button>
          ))}
        </div>
        <button onClick={clearDone} className="hover:text-slate-800">
          Clear done
        </button>
      </div>

      <ul className="mt-4 divide-y divide-slate-100">
        {visible.length === 0 && <li className="py-8 text-center text-slate-400">Nothing here yet.</li>}
        {visible.map((t) => (
          <li key={t.id} className="group flex items-center gap-3 py-3">
            <button
              onClick={() => toggle(t.id)}
              aria-label="toggle"
              className={
                "flex h-5 w-5 items-center justify-center rounded border " +
                (t.done ? "border-{{ACCENT}}-600 bg-{{ACCENT}}-600 text-white" : "border-slate-300")
              }
            >
              {t.done && <Check className="h-3 w-3" />}
            </button>
            <span className={"flex-1 " + (t.done ? "text-slate-400 line-through" : "")}>{t.text}</span>
            <button
              onClick={() => remove(t.id)}
              aria-label="delete"
              className="text-slate-300 opacity-0 hover:text-red-500 group-hover:opacity-100"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
'''

TODO = (
    'import { useEffect, useMemo, useState, type FormEvent } from "react";\n'
    'import { Check, Plus, Trash2 } from "lucide-react";\n'
    + _TODO_CORE
    + r'''
export default function TodoApp() {
  return <TodoBoard storageKey="oa-todos" />;
}
'''
)

TODO_LOGIN = (
    'import { useEffect, useMemo, useState, type FormEvent } from "react";\n'
    'import { Check, LogOut, Plus, Trash2 } from "lucide-react";\n'
    + _TODO_CORE
    + r'''
type Account = { email: string; password: string };
const ACCOUNTS_KEY = "oa-accounts";
const SESSION_KEY = "oa-session";

function loadAccounts(): Account[] {
  try {
    return JSON.parse(localStorage.getItem(ACCOUNTS_KEY) || "[]") as Account[];
  } catch {
    return [];
  }
}

function AuthForm({ onLogin }: { onLogin: (email: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    const accounts = loadAccounts();
    const normalized = email.trim().toLowerCase();
    if (!normalized.includes("@") || password.length < 6) {
      setError("Enter a valid email and a password of at least 6 characters.");
      return;
    }
    if (mode === "register") {
      if (accounts.some((a) => a.email === normalized)) {
        setError("This email is already registered.");
        return;
      }
      localStorage.setItem(ACCOUNTS_KEY, JSON.stringify([...accounts, { email: normalized, password }]));
    } else if (!accounts.some((a) => a.email === normalized && a.password === password)) {
      setError("Wrong email or password.");
      return;
    }
    localStorage.setItem(SESSION_KEY, normalized);
    onLogin(normalized);
  }

  return (
    <form onSubmit={submit} className="mx-auto max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
      <h2 className="text-xl font-semibold">{mode === "login" ? "Sign in" : "Create account"}</h2>
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@example.com"
        className="w-full rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-{{ACCENT}}-500"
      />
      <input
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Password"
        className="w-full rounded-lg border border-slate-200 px-3 py-2 outline-none focus:border-{{ACCENT}}-500"
      />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" className="w-full rounded-lg bg-{{ACCENT}}-600 py-2 font-medium text-white hover:bg-{{ACCENT}}-700">
        {mode === "login" ? "Sign in" : "Sign up"}
      </button>
      <button
        type="button"
        onClick={() => {
          setMode(mode === "login" ? "register" : "login");
          setError("");
        }}
        className="w-full text-sm text-slate-500 hover:text-slate-800"
      >
        {mode === "login" ? "No account? Create one" : "Have an account? Sign in"}
      </button>
    </form>
  );
}

export default function TodoApp() {
  const [user, setUser] = useState<string | null>(() => localStorage.getItem(SESSION_KEY));
  if (!user) return <AuthForm onLogin={setUser} />;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-sm text-slate-500">
        <span>
          Signed in as <b className="text-slate-800">{user}</b>
        </span>
        <button
          onClick={() => {
            localStorage.removeItem(SESSION_KEY);
            setUser(null);
          }}
          className="inline-flex items-center gap-1 hover:text-slate-800"
        >
          <LogOut className="h-4 w-4" /> Sign out
        </button>
      </div>
      <TodoBoard key={user} storageKey={"oa-todos:" + user} />
    </div>
  );
}
'''
)

POMODORO = r'''import { useEffect, useRef, useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";

type Mode = "focus" | "short" | "long";
const DURATIONS: Record<Mode, number> = { focus: 25 * 60, short: 5 * 60, long: 15 * 60 };
const LABELS: Record<Mode, string> = { focus: "Focus", short: "Short break", long: "Long break" };

function fmt(sec: number): string {
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = (sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export default function Pomodoro() {
  const [mode, setMode] = useState<Mode>("focus");
  const [left, setLeft] = useState(DURATIONS.focus);
  const [running, setRunning] = useState(false);
  const [completed, setCompleted] = useState(0);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!running) return;
    timer.current = window.setInterval(() => setLeft((l) => Math.max(0, l - 1)), 1000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [running]);

  useEffect(() => {
    document.title = `${fmt(left)} · ${LABELS[mode]}`;
    if (left > 0) return;
    setRunning(false);
    if (mode === "focus") {
      const n = completed + 1;
      setCompleted(n);
      switchMode(n % 4 === 0 ? "long" : "short");
    } else {
      switchMode("focus");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left]);

  function switchMode(m: Mode) {
    setMode(m);
    setLeft(DURATIONS[m]);
    setRunning(false);
  }

  const total = DURATIONS[mode];
  const progress = 1 - left / total;
  const r = 90;
  const c = 2 * Math.PI * r;

  return (
    <div className="flex flex-col items-center rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200">
      <div className="flex gap-2">
        {(Object.keys(DURATIONS) as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => switchMode(m)}
            className={
              "rounded-full px-4 py-1.5 text-sm " +
              (mode === m ? "bg-{{ACCENT}}-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200")
            }
          >
            {LABELS[m]}
          </button>
        ))}
      </div>

      <div className="relative my-8 h-56 w-56">
        <svg viewBox="0 0 200 200" className="h-full w-full -rotate-90">
          <circle cx="100" cy="100" r={r} className="fill-none stroke-slate-100" strokeWidth="10" />
          <circle
            cx="100"
            cy="100"
            r={r}
            className="fill-none stroke-{{ACCENT}}-500 transition-all duration-500"
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={c * (1 - progress)}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-mono text-5xl font-semibold tabular-nums">{fmt(left)}</span>
          <span className="mt-1 text-sm text-slate-500">{LABELS[mode]}</span>
        </div>
      </div>

      <div className="flex gap-3">
        <button
          onClick={() => setRunning((v) => !v)}
          className="inline-flex items-center gap-2 rounded-xl bg-{{ACCENT}}-600 px-6 py-3 font-medium text-white hover:bg-{{ACCENT}}-700"
        >
          {running ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
          {running ? "Pause" : "Start"}
        </button>
        <button
          onClick={() => switchMode(mode)}
          className="inline-flex items-center gap-2 rounded-xl bg-slate-100 px-4 py-3 text-slate-700 hover:bg-slate-200"
        >
          <RotateCcw className="h-5 w-5" /> Reset
        </button>
      </div>
      <p className="mt-6 text-sm text-slate-500">
        Completed sessions: <b className="text-slate-800">{completed}</b>
      </p>
    </div>
  );
}
'''

COUNTER = r'''import { useState } from "react";
import { Minus, Plus, RotateCcw, Undo2 } from "lucide-react";

type Change = { delta: number; at: number };

export default function Counter() {
  const [count, setCount] = useState(0);
  const [step, setStep] = useState(1);
  const [history, setHistory] = useState<Change[]>([]);

  function apply(delta: number) {
    setCount((c) => c + delta);
    setHistory((h) => [{ delta, at: Date.now() }, ...h].slice(0, 20));
  }

  function undo() {
    const [last, ...rest] = history;
    if (!last) return;
    setCount((c) => c - last.delta);
    setHistory(rest);
  }

  return (
    <div className="rounded-2xl bg-white p-8 shadow-sm ring-1 ring-slate-200">
      <div className="text-center font-mono text-7xl font-bold tabular-nums text-{{ACCENT}}-600">{count}</div>
      <div className="mt-8 flex items-center justify-center gap-3">
        <button onClick={() => apply(-step)} className="rounded-xl bg-slate-100 p-4 hover:bg-slate-200" aria-label="decrement">
          <Minus className="h-6 w-6" />
        </button>
        <button
          onClick={() => apply(step)}
          className="rounded-xl bg-{{ACCENT}}-600 p-4 text-white hover:bg-{{ACCENT}}-700"
          aria-label="increment"
        >
          <Plus className="h-6 w-6" />
        </button>
      </div>
      <div className="mt-6 flex items-center justify-center gap-2 text-sm">
        <span className="text-slate-500">Step</span>
        {[1, 5, 10].map((s) => (
          <button
            key={s}
            onClick={() => setStep(s)}
            className={"rounded-md px-3 py-1 " + (step === s ? "bg-{{ACCENT}}-50 text-{{ACCENT}}-700" : "bg-slate-100")}
          >
            {s}
          </button>
        ))}
        <button onClick={undo} disabled={!history.length} className="ml-4 inline-flex items-center gap-1 text-slate-600 disabled:opacity-40">
          <Undo2 className="h-4 w-4" /> Undo
        </button>
        <button
          onClick={() => {
            setCount(0);
            setHistory([]);
          }}
          className="inline-flex items-center gap-1 text-slate-600"
        >
          <RotateCcw className="h-4 w-4" /> Reset
        </button>
      </div>
      {history.length > 0 && (
        <ul className="mt-6 max-h-40 overflow-auto rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
          {history.map((h) => (
            <li key={h.at + ":" + h.delta} className="flex justify-between">
              <span>{h.delta > 0 ? `+${h.delta}` : h.delta}</span>
              <span>{new Date(h.at).toLocaleTimeString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
'''

LANDING = r'''import { useState, type FormEvent } from "react";
import { ChevronDown, Rocket, ShieldCheck, Sparkles, Zap } from "lucide-react";

const TITLE = {{TITLE}};

const FEATURES = [
  { icon: Zap, title: "Fast", body: "Loads instantly and feels snappy on every device." },
  { icon: ShieldCheck, title: "Reliable", body: "Built on a modern, well-tested stack." },
  { icon: Sparkles, title: "Delightful", body: "Thoughtful details that make people smile." },
];

const FAQ = [
  { q: "How do I get started?", a: "Join the waitlist below and we will reach out with early access." },
  { q: "Is it free?", a: "There is a generous free tier; paid plans unlock advanced features." },
  { q: "Can I use it on mobile?", a: "Yes. The experience is fully responsive." },
];

export default function Landing() {
  const [email, setEmail] = useState("");
  const [joined, setJoined] = useState(false);
  const [open, setOpen] = useState<number | null>(0);

  function join(e: FormEvent) {
    e.preventDefault();
    if (email.includes("@")) setJoined(true);
  }

  return (
    <div className="space-y-10">
      <section className="rounded-3xl bg-gradient-to-br from-{{ACCENT}}-600 to-{{ACCENT}}-800 p-10 text-white shadow-lg">
        <Rocket className="h-10 w-10 opacity-90" />
        <h2 className="mt-4 text-4xl font-bold leading-tight">{TITLE}</h2>
        <p className="mt-3 max-w-xl text-white/80">Everything you need, nothing you don't. Start in minutes.</p>
        <form onSubmit={join} className="mt-6 flex max-w-md gap-2">
          {joined ? (
            <p className="rounded-lg bg-white/15 px-4 py-2">Thanks! You're on the list.</p>
          ) : (
            <>
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="flex-1 rounded-lg px-3 py-2 text-slate-900 outline-none"
              />
              <button className="rounded-lg bg-white px-4 py-2 font-medium text-{{ACCENT}}-700">Join waitlist</button>
            </>
          )}
        </form>
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        {FEATURES.map((f) => (
          <div key={f.title} className="rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
            <f.icon className="h-6 w-6 text-{{ACCENT}}-600" />
            <h3 className="mt-3 font-semibold">{f.title}</h3>
            <p className="mt-1 text-sm text-slate-500">{f.body}</p>
          </div>
        ))}
      </section>

      <section className="rounded-2xl bg-white p-2 shadow-sm ring-1 ring-slate-200">
        {FAQ.map((item, i) => (
          <div key={item.q} className="border-b border-slate-100 last:border-0">
            <button onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-center justify-between p-4 text-left font-medium">
              {item.q}
              <ChevronDown className={"h-4 w-4 transition " + (open === i ? "rotate-180" : "")} />
            </button>
            {open === i && <p className="px-4 pb-4 text-sm text-slate-500">{item.a}</p>}
          </div>
        ))}
      </section>
    </div>
  );
}
'''


FEATURE_LISTS = {
    "todo": ["Add tasks", "Mark tasks complete", "Delete tasks", "Filter all / active / done",
             "Persist tasks in localStorage"],
    "pomodoro": ["25/5/15 minute modes", "Start / pause / reset", "Circular progress ring",
                 "Auto-switch to breaks", "Completed session counter"],
    "counter": ["Increment / decrement", "Configurable step (1/5/10)", "Undo last change", "Reset",
                "Recent change history"],
    "landing": ["Hero with clear value proposition", "Waitlist email capture", "Feature highlights",
                "FAQ accordion", "Responsive layout"],
}


def features(kind: str, req: str) -> list[str]:
    f = list(FEATURE_LISTS[kind])
    if kind == "todo" and wants_login(req):
        f = ["Sign up / sign in (per-user data)", *f, "Sign out"]
    return f


# ---------------------------------------------------------------- runtime-error scenario (checkui repair loop)
INJECTED_MARK = "OA_INJECTED_RUNTIME_ERROR"

TELEMETRY_BROKEN = """import { useEffect } from "react";

// Reports a page view on mount.
export default function Telemetry() {
  useEffect(() => {
    const cfg = (window as unknown as { __telemetry?: { endpoint: string } }).__telemetry;
    if (!cfg) throw new Error("OA_INJECTED_RUNTIME_ERROR: telemetry config missing");
    console.info("page view", cfg.endpoint);
  }, []);
  return null;
}
"""

TELEMETRY_FIXED = """import { useEffect } from "react";

// Reports a page view on mount; telemetry is optional and must never break the app.
export default function Telemetry() {
  useEffect(() => {
    const cfg = (window as unknown as { __telemetry?: { endpoint: string } }).__telemetry;
    if (!cfg) return;
    console.info("page view", cfg.endpoint);
  }, []);
  return null;
}
"""


def wants_runtime_error(req: str) -> bool:
    r = req.lower()
    return "runtime error" in r or "throw in useeffect" in r or "运行时错误" in req


def with_telemetry(app: str) -> str:
    app = 'import Telemetry from "./components/Telemetry";\n' + app
    return app.replace("      <main ", "      <Telemetry />\n      <main ", 1)
