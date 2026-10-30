"""React Native (Expo) apps written by `oa-mock` for projects on the `expo-app` template.

Each kind is a self-contained `App.tsx` using only react-native primitives so `expo export --platform web`
builds without extra dependencies. `{{ACCENT}}` is replaced with a hex color.
"""
from __future__ import annotations

import json

from oa.llm import mock_apps as apps

ACCENT_HEX = {"blue": "#2563eb", "emerald": "#059669", "violet": "#7c3aed", "amber": "#d97706", "rose": "#e11d48"}

_SHELL = r'''const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#f4f5f7" },
  wrap: { flex: 1, width: "100%", maxWidth: 480, alignSelf: "center", padding: 20 },
  title: { fontSize: 28, fontWeight: "700", color: "#0f172a" },
  subtitle: { marginTop: 4, marginBottom: 16, fontSize: 14, color: "#64748b" },
  card: { backgroundColor: "#fff", borderRadius: 16, padding: 16, shadowColor: "#000", shadowOpacity: 0.05,
          shadowRadius: 8, shadowOffset: { width: 0, height: 2 } },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  input: { flex: 1, borderWidth: 1, borderColor: "#e2e8f0", borderRadius: 10, paddingHorizontal: 12,
           paddingVertical: 10, fontSize: 15, backgroundColor: "#fff" },
  button: { backgroundColor: ACCENT, borderRadius: 10, paddingHorizontal: 16, paddingVertical: 11, alignItems: "center" },
  buttonText: { color: "#fff", fontWeight: "600", fontSize: 15 },
  ghost: { borderRadius: 10, paddingHorizontal: 16, paddingVertical: 11, alignItems: "center", backgroundColor: "#eef2f7" },
  ghostText: { color: "#334155", fontWeight: "600", fontSize: 15 },
  muted: { color: "#94a3b8", fontSize: 13 },
});
'''

TODO = r'''import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import { FlatList, Pressable, SafeAreaView, StyleSheet, Text, TextInput, View } from "react-native";

const ACCENT = "{{ACCENT}}";

type Todo = { id: string; text: string; done: boolean };

export default function App() {
  const [todos, setTodos] = useState<Todo[]>([]);
  const [text, setText] = useState("");
  const add = () => {
    const t = text.trim();
    if (!t) return;
    setTodos((xs) => [{ id: String(Date.now()), text: t, done: false }, ...xs]);
    setText("");
  };
  const left = todos.filter((t) => !t.done).length;
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.wrap}>
        <Text style={styles.title}>{{TITLE}}</Text>
        <Text style={styles.subtitle}>{left} task(s) left</Text>
        <View style={[styles.card, { gap: 12 }]}>
          <View style={styles.row}>
            <TextInput style={styles.input} value={text} onChangeText={setText} placeholder="What needs doing?"
              onSubmitEditing={add} returnKeyType="done" />
            <Pressable style={styles.button} onPress={add} accessibilityRole="button">
              <Text style={styles.buttonText}>Add</Text>
            </Pressable>
          </View>
          <FlatList
            data={todos}
            keyExtractor={(t) => t.id}
            ListEmptyComponent={<Text style={styles.muted}>Nothing yet — add your first task.</Text>}
            renderItem={({ item }) => (
              <View style={[styles.row, { paddingVertical: 10, borderBottomWidth: 1, borderColor: "#f1f5f9" }]}>
                <Pressable
                  onPress={() => setTodos((xs) => xs.map((t) => (t.id === item.id ? { ...t, done: !t.done } : t)))}
                  style={{ width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: ACCENT,
                           backgroundColor: item.done ? ACCENT : "transparent" }}
                  accessibilityRole="checkbox"
                />
                <Text style={{ flex: 1, fontSize: 15, color: item.done ? "#94a3b8" : "#0f172a",
                               textDecorationLine: item.done ? "line-through" : "none" }}>{item.text}</Text>
                <Pressable onPress={() => setTodos((xs) => xs.filter((t) => t.id !== item.id))}>
                  <Text style={styles.muted}>Delete</Text>
                </Pressable>
              </View>
            )}
          />
        </View>
      </View>
      <StatusBar style="auto" />
    </SafeAreaView>
  );
}

''' + _SHELL

POMODORO = r'''import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import { Pressable, SafeAreaView, StyleSheet, Text, View } from "react-native";

const ACCENT = "{{ACCENT}}";
const MODES = { focus: 25 * 60, short: 5 * 60, long: 15 * 60 } as const;
type Mode = keyof typeof MODES;

export default function App() {
  const [mode, setMode] = useState<Mode>("focus");
  const [left, setLeft] = useState<number>(MODES.focus);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setLeft((s) => (s > 0 ? s - 1 : 0)), 1000);
    return () => clearInterval(id);
  }, [running]);
  useEffect(() => {
    if (left === 0 && running) {
      setRunning(false);
      if (mode === "focus") setDone((d) => d + 1);
    }
  }, [left, running, mode]);
  const pick = (m: Mode) => { setMode(m); setLeft(MODES[m]); setRunning(false); };
  const mm = String(Math.floor(left / 60)).padStart(2, "0");
  const ss = String(left % 60).padStart(2, "0");
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.wrap}>
        <Text style={styles.title}>{{TITLE}}</Text>
        <Text style={styles.subtitle}>Focus 25 min · break 5 min</Text>
        <View style={[styles.card, { alignItems: "center", gap: 20 }]}>
          <View style={styles.row}>
            {(Object.keys(MODES) as Mode[]).map((m) => (
              <Pressable key={m} onPress={() => pick(m)}
                style={[styles.ghost, mode === m && { backgroundColor: ACCENT }]}>
                <Text style={[styles.ghostText, mode === m && { color: "#fff" }]}>{m}</Text>
              </Pressable>
            ))}
          </View>
          <Text style={{ fontSize: 64, fontWeight: "700", color: "#0f172a", fontVariant: ["tabular-nums"] }}>{mm}:{ss}</Text>
          <View style={styles.row}>
            <Pressable style={styles.button} onPress={() => setRunning((r) => !r)}>
              <Text style={styles.buttonText}>{running ? "Pause" : "Start"}</Text>
            </Pressable>
            <Pressable style={styles.ghost} onPress={() => pick(mode)}>
              <Text style={styles.ghostText}>Reset</Text>
            </Pressable>
          </View>
          <Text style={styles.muted}>Completed sessions: {done}</Text>
        </View>
      </View>
      <StatusBar style="auto" />
    </SafeAreaView>
  );
}

''' + _SHELL

COUNTER = r'''import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import { Pressable, SafeAreaView, StyleSheet, Text, View } from "react-native";

const ACCENT = "{{ACCENT}}";

export default function App() {
  const [count, setCount] = useState(0);
  const [step, setStep] = useState(1);
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.wrap}>
        <Text style={styles.title}>{{TITLE}}</Text>
        <Text style={styles.subtitle}>Tap to count</Text>
        <View style={[styles.card, { alignItems: "center", gap: 20 }]}>
          <Text style={{ fontSize: 72, fontWeight: "700", color: ACCENT }}>{count}</Text>
          <View style={styles.row}>
            <Pressable style={styles.ghost} onPress={() => setCount((c) => c - step)}><Text style={styles.ghostText}>−{step}</Text></Pressable>
            <Pressable style={styles.button} onPress={() => setCount((c) => c + step)}><Text style={styles.buttonText}>+{step}</Text></Pressable>
          </View>
          <View style={styles.row}>
            {[1, 5, 10].map((s) => (
              <Pressable key={s} onPress={() => setStep(s)} style={[styles.ghost, step === s && { backgroundColor: ACCENT }]}>
                <Text style={[styles.ghostText, step === s && { color: "#fff" }]}>step {s}</Text>
              </Pressable>
            ))}
          </View>
          <Pressable onPress={() => setCount(0)}><Text style={styles.muted}>Reset</Text></Pressable>
        </View>
      </View>
      <StatusBar style="auto" />
    </SafeAreaView>
  );
}

''' + _SHELL

LANDING = r'''import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import { Pressable, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

const ACCENT = "{{ACCENT}}";
const FEATURES = [
  { title: "Fast", body: "Opens instantly and works offline-first." },
  { title: "Simple", body: "One clear action on every screen." },
  { title: "Yours", body: "Your data stays on your device." },
];

export default function App() {
  const [email, setEmail] = useState("");
  const [joined, setJoined] = useState(false);
  return (
    <SafeAreaView style={styles.safe}>
      <ScrollView contentContainerStyle={[styles.wrap, { gap: 16 }]}>
        <View style={{ backgroundColor: ACCENT, borderRadius: 20, padding: 24, gap: 8 }}>
          <Text style={{ color: "#fff", fontSize: 30, fontWeight: "800" }}>{{TITLE}}</Text>
          <Text style={{ color: "rgba(255,255,255,0.85)", fontSize: 15 }}>The mobile companion you have been waiting for.</Text>
        </View>
        {FEATURES.map((f) => (
          <View key={f.title} style={styles.card}>
            <Text style={{ fontSize: 17, fontWeight: "700", color: "#0f172a" }}>{f.title}</Text>
            <Text style={[styles.muted, { marginTop: 4, fontSize: 14 }]}>{f.body}</Text>
          </View>
        ))}
        <View style={[styles.card, { gap: 10 }]}>
          <Text style={{ fontSize: 17, fontWeight: "700" }}>{joined ? "You're on the list 🎉" : "Join the waitlist"}</Text>
          {!joined && (
            <View style={styles.row}>
              <TextInput style={styles.input} value={email} onChangeText={setEmail} placeholder="you@example.com"
                keyboardType="email-address" autoCapitalize="none" />
              <Pressable style={styles.button} onPress={() => email.includes("@") && setJoined(true)}>
                <Text style={styles.buttonText}>Join</Text>
              </Pressable>
            </View>
          )}
        </View>
      </ScrollView>
      <StatusBar style="auto" />
    </SafeAreaView>
  );
}

''' + _SHELL


def app_tsx(kind: str, req: str, accent: str = "blue") -> str:
    src = {"todo": TODO, "pomodoro": POMODORO, "counter": COUNTER, "landing": LANDING}[kind]
    title = apps.app_title(kind, req) if kind != "landing" else apps.landing_title(req)
    return (src.replace("{{ACCENT}}", ACCENT_HEX.get(accent, accent))
               .replace("{{TITLE}}", "{" + json.dumps(title, ensure_ascii=False) + "}"))
