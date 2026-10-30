// Wire types — mirror docs/CONTRACT.md §1–§3.
export type Role =
  | "User"
  | "Team Leader"
  | "Product Manager"
  | "Architect"
  | "Engineer"
  | "Data Analyst"
  | "Deep Researcher"
  | "System";

export type MessageType =
  | "message"
  | "think"
  | "task"
  | "emoji"
  | "questions_card"
  | "plan_ready"
  | "error"
  | "quota"
  | "restore"
  | "remix"
  | "version"
  | "boost"
  | "automations_config";

export type ChatRuntimeStatus = "running" | "stopping" | "sleeping" | "stopped" | "activating" | "failed";

export type DeltaOp =
  | { insert: string }
  | { insert: { mentiontrigger: { id: string; char: string; value: string } } }
  | { insert: { attachment: { name: string; url: string; mime?: string } } }
  | { action_data: { uuid: string } };
export type Delta = DeltaOp[];

export interface User {
  id: number;
  email: string;
  name: string;
  avatar: string | null;
  plan: string;
  email_verified: boolean;
  created_at: string;
  current_workspace_id: number | null;
}

export interface Workspace {
  id: number;
  name: string;
  owner_id: number;
  plan: string;
  role: "owner" | "admin" | "member" | "viewer";
  created_at: string;
}

export interface Member {
  user_id: number;
  email: string;
  name: string;
  avatar: string | null;
  role: Workspace["role"];
}

export interface ChatConfig {
  agent_mode: "full" | "lite";
  template_name?: string;
  framework?: string;
  plan_mode?: boolean;
  github?: { repo: string; branch: string } | null;
  supabase?: { project_ref: string; url: string } | null;
  cloud_enabled?: boolean;
  [k: string]: unknown;
}

export interface Chat {
  chat_id: string;
  title: string;
  workspace_id: number;
  user_id: number;
  status: "ready" | "forking" | "failed";
  access_mode: "private" | "secret" | "public";
  folder_id: number | null;
  is_starred: boolean;
  fork_from: unknown;
  parent_chat_id?: string | null;
  created_at: string;
  updated_at: string;
  config: ChatConfig;
  llm: { default_model: string };
  metadata: { status: ChatRuntimeStatus; is_running: boolean; preview_port: number };
  uris: { dev: string | null; prod: string | null };
  version: { version: string; summary: string; cover: string | null; link: string | null } | null;
  stats: { likes: number; views: number };
  is_liked: boolean;
}

export interface Message {
  id: number;
  uuid: string;
  chat_id: string;
  role: Role | string;
  name: string;
  type: MessageType | string;
  content: any;
  refer_id: number | null;
  round_id: number;
  attempt: number;
  attempts: number;
  is_finished: boolean;
  metadata: Record<string, any>;
  user_id: number | null;
  created_at: string;
  updated_at: string;
  reply_messages: Message[];
}

export interface ActionData {
  id: number;
  uuid: string;
  chat_id: string;
  message_uuid: string | null;
  command_name: string;
  block_type: string;
  file: string | null;
  src_path: string | null;
  extra: Record<string, any>;
  status: "running" | "success" | "failed";
  timeline_index: number | null;
  created_at: string;
  updated_at: string;
}

export interface BlockTimeline {
  id: number;
  chat_id: string;
  index: number;
  block_type: string;
  block_owner: string;
  command_name: string;
  file: string | null;
  src_path: string | null;
  content: string;
  is_running: boolean;
  extra_data: Record<string, any> | null;
  version: string | null;
  created_at: string;
}

export interface Task {
  id: string;
  title: string;
  assignee: string;
  status: "pending" | "running" | "done";
  instruction?: string;
}

export interface QueueItem {
  id: number;
  position: number;
  content: Delta;
  metadata: Record<string, any>;
  status: "queued" | "sent";
  created_at: string;
}

export interface Version {
  version: string;
  summary: string;
  cover: string | null;
  link: string | null;
  commit: string | null;
  status: "building" | "ready" | "failed";
  created_at: string;
}

export interface Deployment {
  chat_id: string;
  version: string;
  is_always_latest: boolean;
  access_mode?: "public" | "secret" | "private";
  status: "deploying" | "success" | "failed";
  url: string | null;
  seq: number;
  updated_at: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size?: number;
  mtime?: number | string;
}
export interface TreeNode {
  name: string;
  path: string;
  type: "file" | "dir";
  children?: TreeNode[];
}

export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  description?: string;
}
export interface GeneralConfig {
  basic_model: string;
  default_model: string;
  adv_models: string[];
  agent_mode: "full" | "lite";
  models: ModelInfo[];
}

export interface Page<T> {
  items: T[];
  total: number;
  has_more: boolean;
}

export interface SendMessageBody {
  content: Delta;
  type?: "message";
  refer_id?: number;
  metadata?: {
    agent_mode?: "full" | "lite";
    default_model?: string;
    plan_mode?: boolean;
    attachments?: { name: string; path: string; url: string; mime: string }[];
    task_reply?: { message_id: number; option_index: number };
    /** visual-edit "Ask AI about this": element picked in the preview */
    selection?: DesignSelectionRef;
    /** Deep Research mode: Iris writes a cited report */
    enable_dr?: boolean;
  };
  /** Best-of-N: build the prompt with each model in parallel candidate projects */
  use_boost?: boolean;
  boost_models?: string[];
}

/** Element picked in the preview by bridge.js design mode (appviewer:sync-design-selection). */
export interface DesignSelection {
  selector: string;
  loc: string | null;
  tag: string;
  text: string;
  text_editable: boolean;
  classes: string[];
  rect: { x: number; y: number; width: number; height: number };
  styles: Record<string, string>;
  path: string;
}

export type DesignSelectionRef = Pick<DesignSelection, "loc" | "selector" | "tag" | "text" | "classes">;

export interface StagedEdit {
  loc: string;
  add: string[];
  remove: string[];
  text: string | null;
  selector?: string;
  tag?: string;
}

export interface BoostItem {
  index: number;
  model: string;
  chat_id: string;
  status: "queued" | "running" | "success" | "failed" | "cancelled";
  summary: string;
  preview_url: string | null;
  version: string | null;
  version_link: string | null;
  build_ok: boolean | null;
  checkui_ok: boolean | null;
  steps?: number;
  error?: string | null;
}

export interface Boost {
  id: number;
  chat_id: string;
  status: "running" | "ready" | "merging" | "merged" | "aborted" | "failed";
  items: BoostItem[];
  recommended: number | null;
  selected: number | null;
  judge_reason: string;
  message_id: number | null;
  card_message_id: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Automation {
  id: number;
  chat_id: string;
  chat_title: string | null;
  workspace_id: number;
  name: string;
  prompt: string;
  cron: string;
  schedule: string;
  timezone: string;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: "sent" | "queued" | "failed" | null;
  last_message_id: number | null;
  run_count: number;
  created_at: string | null;
}

/** Items of Assets / File blocks (imagecreator_*, imageanalyzer_*, pdfanalyzer_*, documentextractor_*, markdownpdfcreator_*). */
export interface AssetItem {
  path: string;
  kind: "image" | "pdf" | "document";
  preview?: string | null;
  url?: string | null;
  prompt?: string;
  width?: number;
  height?: number;
  pages?: number;
  chars?: number;
  excerpt?: string;
  dominant_colors?: { hex: string; share: number }[];
  tone?: string;
  source?: string;
}
