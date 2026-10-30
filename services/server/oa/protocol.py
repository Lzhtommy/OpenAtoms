"""Shared protocol enums (CONTRACT §1.4/§2/§3). `make protocol` generates packages/protocol TS types from this file."""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    USER = "User"
    TEAM_LEADER = "Team Leader"
    PRODUCT_MANAGER = "Product Manager"
    ARCHITECT = "Architect"
    ENGINEER = "Engineer"
    DATA_ANALYST = "Data Analyst"
    DEEP_RESEARCHER = "Deep Researcher"
    SEO_SPECIALIST = "SEO Specialist"
    ADS_SPECIALIST = "Ads Specialist"
    SYSTEM = "System"


class MessageType(str, Enum):
    MESSAGE = "message"
    THINK = "think"
    TASK = "task"
    EMOJI = "emoji"
    QUESTIONS_CARD = "questions_card"
    PLAN_READY = "plan_ready"
    ERROR = "error"
    QUOTA = "quota"
    RESTORE = "restore"
    REMIX = "remix"
    VERSION = "version"
    BOOST = "boost"
    AUTOMATIONS_CONFIG = "automations_config"


class BlockType(str, Enum):
    EDITOR = "Editor"
    TERMINAL = "Terminal"
    TASK = "Task"
    BROWSER = "Browser"
    BROWSER_RT = "Browser-RT"
    NOTEBOOK = "Notebook"
    SEARCH = "Search"
    BACKEND = "Backend"
    ASSETS = "Assets"
    FILE = "File"
    RESEARCH = "Research"
    GROWTH = "Growth"


class CommandName(str, Enum):
    PLAN_APPEND_TASK = "plan_append_task"
    PLAN_RESET_TASK = "plan_reset_task"
    PLAN_FINISH_CURRENT_TASK = "plan_finish_current_task"
    MENTION = "mention"
    ASK_HUMAN = "ask_human"
    ASK_QUESTIONS = "ask_questions"
    REPLY_TO_HUMAN = "reply_to_human"
    EDITOR_READ = "editor_read"
    EDITOR_WRITE = "editor_write"
    EDITOR_EDIT_FILE_BY_REPLACE = "editor_edit_file_by_replace"
    EDITOR_APPEND_FILE = "editor_append_file"
    TERMINAL_RUN = "terminal_run"
    CHECKUI_RUN = "checkui_run"
    BROWSER_GOTO = "browser_goto"
    BROWSER_CLICK = "browser_click"
    BROWSER_SCROLL = "browser_scroll"
    BROWSER_TYPE = "browser_type"
    DATAANALYST_WRITE_AND_EXEC_CODE = "dataanalyst_write_and_exec_code"
    SEARCHENHANCEDQA_RUN = "searchenhancedqa_run"
    BACKENDMANAGER_CREATE_TABLE = "backendmanager_create_table"
    BACKENDMANAGER_GET_SCHEMAS = "backendmanager_get_schemas"
    BACKENDMANAGER_INSERT_TABLE_DATA = "backendmanager_insert_table_data"
    DEPLOYER_DEPLOY_TO_PUBLIC = "deployer_deploy_to_public"
    SEARCHNAVIGATOR_NEXT_QUERY = "searchnavigator_next_query"
    RESEARCHEXECUTOR_FINALIZE_ARTICLE = "researchexecutor_finalize_article"
    SEO_AUDIT = "seo_audit"
    ADS_GENERATE_CAMPAIGN = "ads_generate_campaign"
    IMAGECREATOR_GENERATE_IMAGE = "imagecreator_generate_image"
    IMAGEANALYZER_ANALYZE_IMAGES = "imageanalyzer_analyze_images"
    PDFANALYZER_ANALYZE_PDFS = "pdfanalyzer_analyze_pdfs"
    DOCUMENTEXTRACTOR_EXTRACT_DOCUMENTS = "documentextractor_extract_documents"
    MARKDOWNPDFCREATOR_CONVERT_FILE = "markdownpdfcreator_convert_file"


class ChatRuntimeStatus(str, Enum):
    RUNNING = "running"
    STOPPING = "stopping"
    SLEEPING = "sleeping"
    STOPPED = "stopped"
    ACTIVATING = "activating"
    FAILED = "failed"


class AgentMode(str, Enum):
    LITE = "lite"
    FULL = "full"


SOCKET_EVENTS = [
    "msg:create", "msg:update", "action_data:update", "timeline:start", "timeline:content", "timeline:complete",
    "version:create", "version:update", "version:complete", "build:update", "build:error", "build:complete",
    "deploy:update", "chat:update", "prompt_queue:update", "prompt_queue:item_sent", "chat_error:create",
    "chat_error:delete", "run:start", "run:end", "quota:update",
    "boost:update", "boost:merge_end", "boost:all_end", "automation:update",
]

AGENTS = {
    "Team Leader": "Mike",
    "Product Manager": "Emma",
    "Architect": "Bob",
    "Engineer": "Alex",
    "Data Analyst": "David",
    "Deep Researcher": "Iris",
    "SEO Specialist": "Sarah",
    "Ads Specialist": "Adrian",
}
