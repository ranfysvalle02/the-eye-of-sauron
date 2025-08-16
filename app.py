#!/usr/bin/env python3

import os
import sys
import logging
import json
import re
import queue
import threading
import concurrent.futures
from datetime import datetime
from dotenv import load_dotenv
from openai import AzureOpenAI
from flask import Flask, Response, request, jsonify, render_template_string
import requests
from functools import reduce
import operator

# --- Initialization & Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

# --- Flask App Initialization ---
app = Flask(__name__)
update_queue = queue.Queue()
is_paused_due_to_rate_limit = threading.Event()
is_manually_paused = threading.Event()
is_scan_cancelled = threading.Event()

# --- Keyword Monitoring Configuration (Listeners) ---
DEFAULT_PATTERNS = json.dumps([
    {"pattern": "(?i)mongodb", "label": "MongoDB"},
    {"pattern": "(?i)vector search", "label": "Vector Search"},
    {"pattern": "(?i)voyageai", "label": "VoyageAI"},
])
SEARCH_PATTERNS_JSON = os.getenv("SEARCH_PATTERNS", DEFAULT_PATTERNS)
SEARCH_PATTERNS = {}
patterns_lock = threading.Lock()

# --- API Source Configuration ---
DEFAULT_API_SOURCES = json.dumps([
    {
        "name": "LangChain GitHub Issues",
        "apiUrl": "https://api.github.com/repos/langchain-ai/langchain/issues?state=all&per_page=100&page={PAGE}",
        "httpMethod": "GET",
        "paginationStyle": "page_number",
        "dataRoot": "",
        "fieldMappings": {
            "id": "id", "title": "title", "url": "html_url",
            "text": "body", "by": "user.login", "time": "created_at"
        },
        "fieldsToCheck": ["title", "body"]
    },
    {
        "name": "Hacker News 'AI' Stories",
        "apiUrl": "http://hn.algolia.com/api/v1/search_by_date?query=ai&tags=story&page={PAGE}",
        "httpMethod": "GET",
        "paginationStyle": "page_number",
        "dataRoot": "hits",
        "fieldMappings": {
            "id": "objectID", "title": "title", "url": "url",
            "text": "story_text", "by": "author", "time": "created_at"
        },
        "fieldsToCheck": ["title", "story_text"]
    }
])
API_SOURCES = {}
sources_lock = threading.Lock()

# --- API Source Template Configuration ---
API_SOURCE_TEMPLATES = [
    {
        "id": "hn-search",
        "name": "Hacker News Search",
        "description": "Searches for stories on Hacker News by date.",
        "variables": [
            {"name": "Query", "key": "{QUERY}", "placeholder": "e.g., ai"}
        ],
        "config": {
            "name": "Hacker News '{QUERY}' Stories",
            "apiUrl": "http://hn.algolia.com/api/v1/search_by_date?query={QUERY}&tags=story&page={PAGE}",
            "dataRoot": "hits",
            "fieldMappings": {
                "id": "objectID", "title": "title", "url": "url",
                "text": "story_text", "by": "author", "time": "created_at"
            },
            "fieldsToCheck": ["title", "story_text"]
        }
    },
    {
        "id": "github-issues",
        "name": "GitHub Repo Issues",
        "description": "Fetches all issues from a specific GitHub repository.",
        "variables": [
            {"name": "Owner", "key": "{OWNER}", "placeholder": "e.g., langchain-ai"},
            {"name": "Repo", "key": "{REPO}", "placeholder": "e.g., langchain"}
        ],
        "config": {
            "name": "GitHub Issues for {OWNER}/{REPO}",
            "apiUrl": "https://api.github.com/repos/{OWNER}/{REPO}/issues?state=all&per_page=100&page={PAGE}",
            "dataRoot": "",
            "fieldMappings": {
                "id": "id", "title": "title", "url": "html_url",
                "text": "body", "by": "user.login", "time": "created_at"
            },
            "fieldsToCheck": ["title", "body"]
        }
    }
]

# --- Service Configuration ---
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 25))
PAGES_PER_SCAN = 10  # Scan this many pages before pausing for user input
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "application/vnd.github.v3+json"  # Recommended by GitHub Docs
}

# --- GitHub Personal Access Token (PAT) Configuration ---
GITHUB_PAT = os.getenv("GITHUB_PAT")
if GITHUB_PAT:
    logging.info("✅ GitHub PAT found. Authenticated requests will be used for GitHub APIs.")
else:
    logging.warning("⚠️ GitHub PAT not found. Using unauthenticated requests, which may lead to rate limiting.")

# --- Azure OpenAI Client Configuration ---
try:
    client = AzureOpenAI(
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    )
    DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    if not all([os.getenv("AZURE_OPENAI_ENDPOINT"), os.getenv("AZURE_OPENAI_API_KEY"), DEPLOYMENT]):
        raise ValueError("One or more required Azure environment variables are missing.")
    logging.info(f"Azure OpenAI client configured successfully for deployment: {DEPLOYMENT}")
except Exception as e:
    logging.error(f"Error initializing Azure OpenAI client: {e}")
    client = None

# --- Prompt Templates ---
CONTENT_SUMMARY_SYSTEM_PROMPT = """You are an expert AI analyst. Your task is to analyze the provided content from an API and provide a concise, insightful summary for a business and technical audience.

**Core Directives:**
- **Summarize Key Points:** Distill the main problem, user request, or key discussion points from the content.
- **Ignore Boilerplate:** Disregard markdown, code blocks (unless essential), and irrelevant text.
- **Format for Clarity:** Your final output must be a single, well-written paragraph of no more than 150 words.
- **Be Objective:** Do not add personal opinions, disclaimers, or apologies.
- **Ground Your Answer:** Base your summary *only* on the provided text.
- **Identify Relevance:** Briefly mention why this content might be relevant to the keyword that was matched.

Your summary will be displayed in a web UI, so it must be professional and easy to read."""

# --- Core Application Logic ---
def get_nested_value(data_dict, key_string):
    """Safely retrieves a value from a nested dictionary using dot notation."""
    if not key_string:
        return None
    try:
        return reduce(operator.getitem, key_string.split('.'), data_dict)
    except (KeyError, TypeError, IndexError):
        return None

def get_llm_summary(client, prompt_text, model_deployment):
    if not client:
        return "[Error: OpenAI client not configured]"
    if is_manually_paused.is_set():
        logging.warning("LLM request cancelled due to manual pause.")
        return "[Status: Paused. Request cancelled.]"
    if is_paused_due_to_rate_limit.is_set():
        logging.warning("LLM request blocked due to rate limit pause.")
        return "[Status: Paused due to rate limit. Request not sent.]"
    try:
        response = client.chat.completions.create(
            model=model_deployment,
            messages=[
                {"role": "system", "content": CONTENT_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt_text}
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        error_str = str(e).lower()
        if "rate limit" in error_str:
            logging.warning(f"RATE LIMIT EXCEEDED. Pausing all LLM requests. Error: {e}")
            is_paused_due_to_rate_limit.set()
            reason = "Rate limit exceeded. Please wait a moment before resuming."
            match = re.search(r'try again in ([\d\.]+) seconds', error_str)
            if match:
                reason = f"Rate limit exceeded. The API suggests waiting {match.group(1)} seconds. Please wait and then click 'Resume'."
            update_queue.put({"type": "status", "status": "rate_limit_paused", "reason": reason})
            return f"[Error: Paused due to rate limit: {e}]"
        else:
            logging.error(f"An unexpected error occurred in get_llm_summary: {e}")
            return f"[Error calling LLM: {e}]"

def process_and_queue_api_item(item, matched_label, source_config):
    logging.info(f"✅ API MATCH: Item from '{source_config['name']}' matched '{matched_label}'. Processing...")

    mappings = source_config.get('fieldMappings', {})
    time_str = str(get_nested_value(item, mappings.get('time', '')))
    unix_timestamp = 0
    if time_str:
        try:
            if time_str.isdigit():
                unix_timestamp = int(time_str)
            else:
                # Convert typical ISO or date strings to UTC timestamp
                time_str = time_str.replace('Z', '+00:00')
                if '.' in time_str:
                    # Sometimes time_str includes fractional seconds beyond microseconds, parse carefully
                    parts = time_str.split('.')
                    # Reconstruct if we have extra fraction digits
                    if len(parts) > 1 and len(parts[1]) > 6:
                        time_suffix = parts[1][6:]
                        time_str = parts[0] + '.' + parts[1][:6] + time_suffix
                unix_timestamp = int(datetime.fromisoformat(time_str).timestamp())
        except (ValueError, TypeError) as e:
            logging.warning(f"Could not parse timestamp '{time_str}': {e}")

    item_data = {
        "id": f"{source_config['name']}-{get_nested_value(item, mappings.get('id'))}",
        "type": "api_item",
        "source_name": source_config['name'],
        "by": get_nested_value(item, mappings.get('by')),
        "time": unix_timestamp,
        "title": get_nested_value(item, mappings.get('title')),
        "url": get_nested_value(item, mappings.get('url')),
        "text": get_nested_value(item, mappings.get('text')),
        "matched_label": matched_label,
        "processed_at": datetime.utcnow().isoformat(),
        "summary_status": "pending"
    }
    update_queue.put(item_data)

    if is_manually_paused.is_set() or is_scan_cancelled.is_set():
        logging.info(f"Summary generation for item {item_data['id']} halted due to pause or cancellation.")
        return

    content_to_summarize = f"Title: {item_data['title']}\n\nContent:\n{item_data['text']}"
    prompt_text = (
        f"## Matched Keyword\n`{matched_label}`\n\n"
        f"## API Content to Analyze (from {source_config['name']})\n\n{content_to_summarize}"
    )
    ai_summary = get_llm_summary(client, prompt_text, DEPLOYMENT)
    update_queue.put({
        "type": "summary_update",
        "id": item_data["id"],
        "ai_summary": ai_summary
    })

def check_if_item_matches(item, source_config):
    """Synchronously checks if an item from an API response matches any pattern."""
    if not item:
        return None
    fields_to_check = source_config.get('fieldsToCheck', [])
    content_parts = [str(get_nested_value(item, field) or '') for field in fields_to_check]
    content_to_check = " ".join(content_parts)

    with patterns_lock:
        current_patterns = SEARCH_PATTERNS.copy()

    for label, pattern in current_patterns.items():
        if pattern.search(content_to_check):
            return label
    return None

def perform_api_scan(source_config, start_page=1):
    source_name = source_config.get('name', 'Unknown Source')
    update_queue.put({"type": "status", "status": "scanning", "reason": f"Starting scan for {source_name}...", "source_name": source_name})
    logging.info(f"API SCAN: Starting for source '{source_name}' from page {start_page}...")

    api_url_template = source_config.get('apiUrl')
    if not api_url_template:
        update_queue.put({"type": "status", "status": "error", "reason": f"Missing 'apiUrl' in config for {source_name}"})
        return

    current_page = start_page

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='ApiScan') as executor:
            for i in range(PAGES_PER_SCAN):
                if is_scan_cancelled.is_set():
                    logging.info(f"Scan for {source_name} was cancelled before starting next page fetch.")
                    break

                if is_manually_paused.is_set():
                    logging.info(f"Scan for {source_name} is manually paused. Waiting...")
                    update_queue.put({"type": "status", "status": "manually_paused", "reason": f"Scan paused for {source_name}.", "source_name": source_name})
                    is_manually_paused.wait()
                    continue

                if is_paused_due_to_rate_limit.is_set():
                    logging.warning(f"API scan for {source_name} paused due to rate limit.")
                    return

                api_url = api_url_template.replace("{PAGE}", str(current_page))
                logging.info(f"Fetching page {current_page} from {source_name} ({api_url})...")
                update_queue.put({"type": "status", "status": "scanning", "reason": f"Fetching page {current_page} from {source_name}...", "source_name": source_name})

                request_headers = HEADERS.copy()
                if GITHUB_PAT and "api.github.com" in api_url:
                    request_headers["Authorization"] = f"Bearer {GITHUB_PAT}"

                response = requests.get(api_url, headers=request_headers, timeout=20)
                response.raise_for_status()

                try:
                    data = response.json()
                except json.JSONDecodeError:
                    logging.error(f"Failed to decode JSON from {api_url}")
                    update_queue.put({"type": "status", "status": "error", "reason": f"Could not parse response from {source_name}."})
                    return

                data_root = source_config.get('dataRoot')
                items = get_nested_value(data, data_root) if data_root else data

                if not isinstance(items, list) or not items:
                    logging.info(f"No more items found for source '{source_name}'.")
                    update_queue.put({"type": "status", "status": "idle", "reason": f"Scan of {source_name} complete."})
                    return

                for item in items:
                    if is_scan_cancelled.is_set():
                        break
                    matched_label = check_if_item_matches(item, source_config)
                    if matched_label:
                        executor.submit(process_and_queue_api_item, item, matched_label, source_config)
                
                if is_scan_cancelled.is_set():
                    break

                current_page += 1

            if is_scan_cancelled.is_set():
                logging.info(f"Scan for {source_name} was cancelled by user.")
                update_queue.put({"type": "status", "status": "idle", "reason": f"Scan for {source_name} cancelled."})
                return

            logging.info(f"Scan paused after reaching page limit of {PAGES_PER_SCAN}.")
            update_queue.put({
                "type": "status",
                "status": "scan_paused",
                "source_name": source_name,
                "reason": f"Paused after scanning {PAGES_PER_SCAN} pages.",
                "next_page": current_page
            })

    except requests.RequestException as e:
        logging.error(f"API SCAN: Failed to fetch from {source_name}: {e}")
        update_queue.put({"type": "status", "status": "error", "reason": f"Failed to fetch data: {e}"})
    except Exception as e:
        logging.error(f"API SCAN: An unexpected error occurred: {e}", exc_info=True)
        update_queue.put({"type": "status", "status": "error", "reason": f"An unexpected error occurred: {e}"})


# --- Flask Routes ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/stream')
def stream():
    def event_stream():
        while True:
            try:
                data = update_queue.get(timeout=None)
                yield f"data: {json.dumps(data)}\n\n"
            except queue.Empty:
                continue
    return Response(event_stream(), mimetype="text/event-stream")

def update_search_patterns(patterns_list):
    global SEARCH_PATTERNS
    new_patterns = {}
    for item in patterns_list:
        try:
            new_patterns[item['label']] = re.compile(item['pattern'])
        except (re.error, KeyError) as e:
            logging.error(f"Skipping invalid pattern item {item}: {e}")
            continue
    with patterns_lock:
        SEARCH_PATTERNS = new_patterns
        logging.info(f"Updated search patterns. Now monitoring {len(SEARCH_PATTERNS)} patterns.")

@app.route('/patterns', methods=['GET', 'POST'])
def manage_patterns():
    if request.method == 'GET':
        with patterns_lock:
            patterns_list = [{"label": label, "pattern": pattern.pattern} for label, pattern in SEARCH_PATTERNS.items()]
        return jsonify(patterns_list)
    if request.method == 'POST':
        new_patterns_list = request.get_json()
        if not isinstance(new_patterns_list, list):
            return jsonify({"error": "Invalid data format"}), 400
        update_search_patterns(new_patterns_list)
        return jsonify({"status": "success"})

@app.route('/validate-regex', methods=['POST'])
def validate_regex():
    data = request.get_json()
    pattern = data.get('pattern')
    if pattern is None:
        return jsonify({"valid": False, "error": "No pattern provided."}), 400
    try:
        re.compile(pattern)
        return jsonify({"valid": True})
    except re.error as e:
        return jsonify({"valid": False, "error": str(e)})

def update_api_sources(sources_list):
    global API_SOURCES
    new_sources = {}
    for source in sources_list:
        if 'name' in source:
            new_sources[source['name']] = source
    with sources_lock:
        API_SOURCES = new_sources
        logging.info(f"Updated API Sources. Now have {len(API_SOURCES)} sources configured.")

@app.route('/api-sources', methods=['GET', 'POST'])
def manage_api_sources():
    if request.method == 'GET':
        with sources_lock:
            return jsonify(list(API_SOURCES.values()))
    if request.method == 'POST':
        new_sources_list = request.get_json()
        if not isinstance(new_sources_list, list):
            return jsonify({"error": "Invalid data format"}), 400
        update_api_sources(new_sources_list)
        return jsonify({"status": "success"})

@app.route('/api-source-templates')
def get_api_source_templates():
    return jsonify(API_SOURCE_TEMPLATES)

@app.route('/scan-source', methods=['POST'])
def scan_source():
    data = request.get_json()
    source_name = data.get('source_name')
    start_page = data.get('start_page', 1)

    is_manually_paused.clear()
    is_scan_cancelled.clear()

    if not source_name:
        return jsonify({"error": "Missing 'source_name' in request body"}), 400
    with sources_lock:
        source_config = API_SOURCES.get(source_name)
    if not source_config:
        return jsonify({"error": f"Source '{source_name}' not found."}), 404

    threading.Thread(target=perform_api_scan, args=(source_config, start_page), daemon=True).start()
    return jsonify({"status": f"API scan for source '{source_name}' initiated."})

@app.route('/generate-summary', methods=['POST'])
def generate_summary():
    data = request.get_json()
    if not data or not all(k in data for k in ['title', 'text', 'matched_label', 'source_name']):
        return jsonify({"error": "Missing required data for summary generation."}), 400

    content_to_summarize = f"Title: {data['title']}\n\nContent:\n{data['text']}"
    prompt_text = (
        f"## Matched Keyword\n`{data['matched_label']}`\n\n"
        f"## API Content to Analyze (from {data['source_name']})\n\n{content_to_summarize}"
    )
    summary = get_llm_summary(client, prompt_text, DEPLOYMENT)
    return jsonify({"ai_summary": summary})

@app.route('/pause-scan', methods=['POST'])
def pause_scan():
    is_manually_paused.set()
    logging.info("Scan manually paused by user.")
    return jsonify({"status": "Scan pause signal sent."})

@app.route('/resume-scan', methods=['POST'])
def resume_scan():
    is_manually_paused.clear()
    logging.info("Scan manually resumed by user.")
    return jsonify({"status": "Scan resume signal sent."})

@app.route('/cancel-scan', methods=['POST'])
def cancel_scan():
    is_scan_cancelled.set()
    is_manually_paused.clear()
    logging.info("Scan cancellation signal sent by user.")
    return jsonify({"status": "Scan cancellation signal sent."})

@app.route('/resume-operations', methods=['POST'])
def resume_operations():
    if is_paused_due_to_rate_limit.is_set():
        is_paused_due_to_rate_limit.clear()
        logging.info("Rate limit flag cleared by user. Resuming operations.")
        update_queue.put({"type": "status", "status": "idle", "reason": "Operations resumed by user."})
        return jsonify({"status": "Resumed operations."})
    else:
        return jsonify({"status": "Operations were not paused."})

@app.route('/send-to-slack', methods=['POST'])
def send_to_slack():
    data = request.get_json()
    item = data.get('item')
    webhook_url = data.get('webhookUrl')
    if not item or not webhook_url:
        return jsonify({"error": "Missing item data or webhook URL"}), 400
    try:
        source_name = item.get('source_name', 'Unknown Source')
        title = item.get('title') or f"Item by {item.get('by', 'N/A')}"
        item_url = item.get('url')
        author = item.get('by', 'N/A')
        post_time = datetime.fromtimestamp(item.get('time', 0)).strftime('%B %d, %Y at %I:%M %p UTC')
        ai_summary = item.get('ai_summary', 'No summary available.')
        formatted_summary = ai_summary.replace('\n', '\n> ')

        slack_payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"New '{item.get('matched_label', 'N/A')}' Mention from {source_name}"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*<{item_url}|{title}>*" if item_url else f"*{title}*"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*AI Summary:*\n> {formatted_summary}"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"*Author:* `{author}`"},
                        {"type": "mrkdwn", "text": f"*Posted:* {post_time}"},
                        {"type": "mrkdwn", "text": f"*Source:* <{item_url}|View Source>" if item_url else f"*Source:* {source_name}"}
                    ]
                },
                {"type": "divider"},
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "Sent from The-Eye-Of-Sauron 👁️"}
                    ]
                }
            ]
        }

        response = requests.post(webhook_url, json=slack_payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Successfully sent item {item.get('id')} to Slack.")
        return jsonify({"status": "success"})
    except requests.RequestException as e:
        logging.error(f"Error sending to Slack: {e}")
        return jsonify({"error": f"Failed to send to Slack: {e}"}), 500
    except Exception as e:
        logging.error(f"An unexpected error occurred in send_to_slack: {e}")
        return jsonify({"error": "An unexpected server error occurred."}), 500

@app.route('/preview-api-source', methods=['POST'])
def preview_api_source():
    data = request.get_json()
    api_url_template = data.get('apiUrl')
    if not api_url_template:
        return jsonify({"error": "Missing 'apiUrl'"}), 400

    # Replace {PAGE} with a static '1' for the preview, as this is standard
    api_url = api_url_template.replace("{PAGE}", "1")
    
    logging.info(f"Fetching preview from: {api_url}")

    try:
        request_headers = HEADERS.copy()
        if GITHUB_PAT and "api.github.com" in api_url:
            request_headers["Authorization"] = f"Bearer {GITHUB_PAT}"
        
        response = requests.get(api_url, headers=request_headers, timeout=15)
        response.raise_for_status()
        
        try:
            preview_data = response.json()
            return jsonify(preview_data)
        except json.JSONDecodeError:
            return jsonify({"error": "Response was not valid JSON.", "raw_response": response.text})

    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out after 15 seconds."}), 504
    except requests.exceptions.HTTPError as e:
        error_body = e.response.text
        return jsonify({"error": f"HTTP Error: {e.response.status_code} {e.response.reason}", "raw_response": error_body}), e.response.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch data: {str(e)}"}), 500

# --- HTML Template ---
HTML_TEMPLATE = """<!DOCTYPE html><html lang="en"><head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The-Eye-Of-Sauron 👁️ | API Scanner</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root {
            --brand-green: #00ED64;
            --brand-blue: #3b82f6;
            --brand-yellow: #f59e0b;
            --brand-red: #ef4444;
            --brand-indigo: #818cf8;
            --bg-dark-primary: #121921;
            --bg-dark-secondary: #212934;
            --border-color: #4A5568;
            --text-primary: #F9FAFB;
            --text-secondary: #9ca3af;
        }
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-dark-primary);
            color: var(--text-primary);
        }
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-dark-secondary); }
        ::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 4px; }
        .brand-green { color: var(--brand-green); }
        .brand-dark-bg { background-color: var(--bg-dark-secondary); }
        .brand-border { border-color: var(--border-color); }
        .markdown-content pre { background-color: #0e131a; padding: 1rem; border-radius: 8px; overflow-x: auto; }
        .markdown-content code { background-color: var(--bg-dark-primary); color: var(--text-primary); padding: 0.2rem 0.4rem; border-radius: 4px; }
        .markdown-content p { margin-bottom: 0.5rem; }
        .hidden { display: none; }
        .modal-overlay { transition: opacity 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
        .modal-content { transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
        .sidebar-section { border-top: 1px solid var(--border-color); padding-top: 1rem; margin-top: 1rem; }
        .code-input { font-family: 'Courier New', Courier, monospace; font-size: 0.875rem; }
        .sauron-eye-container { position: relative; width: 150px; height: 150px; }
        .sauron-eye {
            position: absolute;
            width: 100%;
            height: 100%;
            background: radial-gradient(circle, #ff4500 5%, #ff8c00 15%, #ffd700 25%, #000 50%);
            border-radius: 50% / 10%;
            animation: pulse-eye 4s infinite;
        }
        .sauron-eye::before {
            content: '';
            position: absolute;
            top: 47%;
            left: 10%;
            width: 80%;
            height: 6%;
            background: #000;
            border-radius: 50%;
        }
        .scan-beam {
            position: absolute;
            top: 50%;
            left: -50%;
            width: 200%;
            height: 10px;
            background: linear-gradient(90deg, transparent, rgba(0, 237, 100, 0.5), transparent);
            transform-origin: center;
            animation: sweep 3s linear infinite;
        }
        @keyframes pulse-eye {
            0%, 100% { transform: scale(1); box-shadow: 0 0 10px #ff8c00; }
            50% { transform: scale(1.05); box-shadow: 0 0 25px #ff4500, 0 0 40px #ff8c00; }
        }
        @keyframes sweep {
            from { transform: rotate(-20deg); }
            to { transform: rotate(20deg); }
        }
        @keyframes card-enter {
            from { opacity: 0; transform: translateY(30px) scale(0.98); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        .card-enter-animation {
            animation: card-enter 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94) both;
        }
        .scanning-source {
            background-color: rgba(0, 237, 100, 0.1) !important;
            box-shadow: inset 0 0 10px rgba(0, 237, 100, 0.2), 0 0 15px rgba(0, 237, 100, 0.1);
            animation: pulse-border 2s infinite;
        }
        @keyframes pulse-border {
            0%, 100% { border-color: var(--brand-green); }
            50% { border-color: #4A5568; }
        }
        /* New styles for API Previewer */
        .preview-tab-btn {
            padding: 0.5rem 1rem; border-radius: 6px; background-color: var(--bg-dark-secondary);
            border: 1px solid var(--border-color); transition: all 0.2s;
        }
        .preview-tab-btn.active-tab {
            background-color: var(--brand-blue); border-color: var(--brand-blue); color: white; font-weight: 600;
        }
        .json-key { color: #9cdcfe; }
        .json-string { color: #ce9178; }
        .json-number { color: #b5cea8; }
        .json-boolean { color: #569cd6; }
        .json-null { color: #569cd6; }
        .json-value { cursor: pointer; border-radius: 3px; padding: 1px 3px; display: inline-block; }
        .json-value:hover { background-color: rgba(59, 130, 246, 0.3); }
        .selected-json-path { background-color: rgba(59, 130, 246, 0.5); box-shadow: 0 0 5px var(--brand-blue); }
        .regex-match {
            background-color: rgba(245, 158, 11, 0.4); /* yellow-500 with opacity */
            border-radius: 3px;
            font-weight: bold;
        }
        body.path-selected .mapping-target-btn {
            animation: pulse-blue 1.5s infinite;
        }
        @keyframes pulse-blue {
            0%, 100% { background-color: #374151; box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
            50% { background-color: var(--brand-blue); color: white; box-shadow: 0 0 10px 5px rgba(59, 130, 246, 0.4); }
        }
        /* Styles for new checkboxes in previewer */
        .json-entry-label { display: flex; align-items: center; cursor: pointer; width: 100%; border-radius: 3px; padding: 2px 0; }
        .json-entry-label:hover { background-color: rgba(255, 255, 255, 0.05); }
        .json-checkbox { margin-right: 0.75rem; accent-color: var(--brand-green); width: 14px; height: 14px; }
        
        /* New styles for Quick Add accordion */
        #quick-add-toggle .fa-chevron-right { transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
        #quick-add-toggle.active .fa-chevron-right { transform: rotate(90deg); }
        .quick-add-container {
            max-height: 0;
            opacity: 0;
            transition: max-height 0.5s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease-out, margin-top 0.5s ease;
            margin-top: 0 !important;
        }
        .quick-add-container.active {
            max-height: 500px; /* Adjust as needed for content */
            opacity: 1;
            margin-top: 0.75rem !important;
        }
        .quick-add-item {
            opacity: 0;
            transform: translateY(-10px);
            animation: fade-in-slide-up 0.4s forwards;
        }
        @keyframes fade-in-slide-up {
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
    </style>
</head>
<body class="flex flex-col h-screen">
    <header class="flex items-center justify-between p-4 border-b brand-border shadow-lg flex-wrap gap-4">
        <div class="flex items-center space-x-3">
            <h1 class="text-2xl font-bold text-white">The-Eye-Of-Sauron <span class="brand-green">👁️</span></h1>
        </div>
        <div class="flex items-center space-x-4 flex-wrap gap-4">
            <div class="flex items-center space-x-2">
                <input type="password" id="slack-webhook-url" placeholder="Slack Webhook URL"
                    class="w-48 p-2 text-sm rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500 transition-all shadow-inner"
                    title="Enter your Slack Webhook URL to enable sending notifications.">
                <button id="save-webhook-btn"
                    class="px-3 py-2 text-sm font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">Save</button>
            </div>
            <div id="status-indicator" class="flex items-center space-x-2">
                <span id="status-text" class="text-sm text-gray-400">Idle</span>
                <div id="status-dot" class="w-3 h-3 bg-gray-500 rounded-full transition-colors"></div>
            </div>
        </div>
    </header>
    <main class="flex-1 flex flex-col md:flex-row overflow-hidden">
        <aside class="w-full md:w-1/3 lg:w-1/4 p-4 border-r brand-border overflow-y-auto flex flex-col">
            <div>
                <div class="flex justify-between items-center">
                    <h2 class="text-lg font-semibold">Manage Listeners</h2>
                    <button id="show-add-listener-modal-btn"
                        class="px-3 py-1 text-sm font-semibold rounded-md bg-green-800 hover:bg-green-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">
                        <i class="fa-solid fa-plus mr-2"></i>New
                    </button>
                </div>
                <div id="listeners-list" class="mt-2 flex-1 space-y-2 overflow-y-auto pr-2"></div>
            </div>
            <div class="sidebar-section">
                <div class="flex justify-between items-center">
                    <h2 class="text-lg font-semibold">Manage API Sources</h2>
                    <button id="show-add-source-modal-btn"
                        class="px-3 py-1 text-sm font-semibold rounded-md bg-blue-800 hover:bg-blue-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">
                        <i class="fa-solid fa-plus mr-2"></i>New
                    </button>
                </div>
                <div class="flex items-center gap-2 mt-2">
                    <button id="scan-all-sources-btn"
                        class="px-3 py-1 text-sm font-semibold rounded-md bg-green-800 hover:bg-green-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">
                        <i class="fa-solid fa-play mr-2"></i>Scan All
                    </button>
                </div>
                <div id="sources-list" class="mt-2 flex-1 space-y-2 overflow-y-auto pr-2"></div>
                
                <div class="mt-4 border-t brand-border pt-4">
                    <button id="quick-add-toggle" class="w-full flex justify-between items-center text-left text-base font-semibold text-indigo-300 hover:text-indigo-200 transition-colors">
                        <span><i class="fa-solid fa-wand-magic-sparkles mr-2"></i> Quick Add from Template</span>
                        <i class="fa-solid fa-chevron-right"></i>
                    </button>
                    <div id="sidebar-source-templates-container" class="quick-add-container mt-2 space-y-3">
                        </div>
                </div>

            </div>
        </aside>
        <div id="feed-container" class="flex-1 h-full overflow-y-auto p-4 md:p-6 lg:p-8 space-y-6">
            <div id="controls-container"
                class="sticky top-4 z-10 p-4 rounded-lg flex flex-col items-center justify-center gap-2 text-center transition-all duration-300">
            </div>
            <div id="placeholder" class="text-center text-gray-500 pt-16 transition-opacity duration-300">
                <i class="fas fa-search fa-3x"></i>
                <p class="mt-4 text-lg">No scan in progress.</p>
                <p class="text-sm">Select an API source from the sidebar and press <i class="fa-solid fa-play"></i> to begin.</p>
            </div>
        </div>
    </main>
    <div id="listener-modal"
        class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60 modal-overlay opacity-0">
        <div class="brand-dark-bg border brand-border rounded-lg shadow-2xl p-6 w-full max-w-lg mx-4 modal-content transform scale-95">
            <form id="listener-form">
                <h3 id="listener-modal-title" class="text-xl font-bold mb-4">Add New Listener</h3>
                <input type="hidden" id="original-listener-label">
                <div class="space-y-4">
                    <div>
                        <label for="listener-label" class="block text-sm font-medium">Label</label>
                        <input type="text" id="listener-label" placeholder="e.g., Bug Reports" required
                            class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500">
                        <p id="listener-label-error" class="text-red-500 text-xs mt-1 h-4"></p>
                    </div>
                    <div>
                        <label for="listener-pattern" class="block text-sm font-medium">Regex Pattern</label>
                        <div class="relative">
                            <input type="text" id="listener-pattern" placeholder="e.g., (?i)bug" required
                                class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500 font-mono text-sm pr-8">
                            <div id="regex-validity-indicator" class="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
                            </div>
                        </div>
                        <p id="listener-pattern-error" class="text-red-500 text-xs mt-1 h-4"></p>
                    </div>
                </div>
                <div class="mt-6 border-t border-gray-700 pt-4">
                    <h4 class="text-base font-semibold mb-2 flex items-center">
                        <i class="fa-solid fa-vial mr-2 text-blue-400"></i>Regex Tester
                    </h4>
                    <div>
                        <label for="regex-test-string" class="block text-sm font-medium text-gray-400">Test String</label>
                        <textarea id="regex-test-string" rows="3" placeholder="Enter some text here to test your pattern..."
                            class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm"></textarea>
                    </div>
                    <div class="mt-2">
                        <label class="block text-sm font-medium text-gray-400">Result</label>
                        <div id="regex-test-result" class="mt-1 w-full p-2 rounded-md bg-gray-900 border brand-border min-h-[4rem] text-sm whitespace-pre-wrap break-words">
                            <span class="text-gray-500">No matches found.</span>
                        </div>
                    </div>
                </div>
                <div class="flex justify-end space-x-3 mt-6">
                    <button type="button" id="cancel-listener-btn" class="px-4 py-2 font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">Cancel</button>
                    <button type="submit" id="save-listener-btn" class="px-4 py-2 font-semibold rounded-md bg-green-800 hover:bg-green-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5 disabled:bg-gray-600 disabled:cursor-not-allowed" disabled>Save Listener</button>
                </div>
            </form>
        </div>
    </div>
    <div id="source-modal"
        class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60 modal-overlay opacity-0">
        <div class="brand-dark-bg border brand-border rounded-lg shadow-2xl p-6 w-full max-w-3xl mx-4 max-h-[90vh] flex flex-col modal-content transform scale-95">
            <h3 id="source-modal-title" class="text-xl font-bold mb-4">Add New API Source</h3>
            <form id="source-form" class="flex-1 overflow-y-auto pr-4 space-y-4">
                <input type="hidden" id="original-source-name">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label for="source-name" class="block text-sm font-medium">Source Name (Unique)</label>
                        <input type="text" id="source-name" placeholder="e.g., Hacker News 'AI' Stories" required
                            class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label for="source-api-url" class="block text-sm font-medium">API URL</label>
                        <div class="flex items-center gap-2 mt-1">
                            <input type="text" id="source-api-url" placeholder="https://api.example.com/data?page={PAGE}" required
                                class="flex-grow w-full p-2 rounded-md bg-gray-800 border brand-border font-mono text-sm">
                            <button type="button" id="fetch-preview-btn" class="px-3 py-2 text-sm font-semibold rounded-md bg-indigo-700 hover:bg-indigo-600 transition-all shadow text-white whitespace-nowrap">
                                <i class="fa-solid fa-wand-magic-sparkles mr-1"></i> Preview
                            </button>
                        </div>
                    </div>
                </div>
                <div id="source-preview-container" class="mt-4 border-t brand-border pt-4 hidden">
                    <h4 class="text-md font-semibold mb-2 flex justify-between items-center">
                        <span>API Response Preview</span>
                        <span id="selected-path-display" class="text-xs font-mono text-gray-400 bg-gray-900 px-2 py-1 rounded"></span>
                    </h4>
                    <div id="preview-status" class="p-3 rounded-md bg-gray-800/50 text-center text-gray-400">
                        Click "Preview" to load sample data from your API.
                    </div>
                    <div id="preview-content" class="hidden">
                        <div class="flex gap-2 mb-2">
                            <button type="button" class="preview-tab-btn active-tab" data-tab="interactive">Interactive Mapper</button>
                            <button type="button" class="preview-tab-btn" data-tab="raw">Raw JSON</button>
                        </div>
                        <div id="preview-interactive-tab" class="preview-tab-content">
                            <p class="text-xs text-gray-400 mb-2">
                                For <b>Field Mappings</b>, click a value, then a target button <i class="fa-solid fa-crosshairs text-blue-400"></i>. For <b>Fields to Check</b>, simply toggle the checkbox next to a key.
                            </p>
                            <div id="interactive-preview-item" class="p-3 rounded-md bg-gray-900/70 border brand-border max-h-64 overflow-y-auto text-sm font-mono"></div>
                        </div>
                        <div id="preview-raw-tab" class="preview-tab-content hidden">
                            <pre class="w-full max-h-64 overflow-auto rounded-md bg-gray-900/70 border brand-border p-3"><code id="raw-json-preview" class="text-xs"></code></pre>
                        </div>
                    </div>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label for="source-data-root" class="block text-sm font-medium">Data Root Path (optional)</label>
                        <input type="text" id="source-data-root" placeholder="e.g., results.data"
                            class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border font-mono text-sm">
                        <p class="text-xs text-gray-400 mt-1">Path to the array of items. Leave blank if the response is the array.</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">Fields to Check for Keywords</label>
                        <div id="fields-to-check-container" class="mt-1 w-full p-2 min-h-[60px] rounded-md bg-gray-800 border brand-border flex flex-wrap gap-2 items-start">
                            </div>
                        <textarea id="source-fields-to-check" class="hidden"></textarea>
                    </div>
                </div>
                <div>
                    <label class="block text-sm font-medium">Field Mappings</label>
                        <p class="text-xs text-gray-400 mb-2">
                            Use the <span class="font-bold text-indigo-400">Previewer</span> above. Click a value, then click the <i class="fa-solid fa-crosshairs text-blue-400"></i> icon for the field you want to map.
                        </p>
                    <div id="mapping-inputs-container" class="space-y-2">
                    </div>
                    <textarea id="source-field-mappings" class="hidden"></textarea>
                </div>
            </form>
            <div class="flex justify-end space-x-3 mt-6 pt-4 border-t brand-border">
                <button type="button" id="cancel-source-btn" class="px-4 py-2 font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">Cancel</button>
                <button type="button" id="save-source-btn" class="px-4 py-2 font-semibold rounded-md bg-blue-800 hover:bg-blue-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">Save Source</button>
            </div>
        </div>
    </div>
    <script>
    document.addEventListener('DOMContentLoaded', () => {
        const ui = {
            feedContainer: document.getElementById('feed-container'),
            statusText: document.getElementById('status-text'),
            statusDot: document.getElementById('status-dot'),
            placeholder: document.getElementById('placeholder'),
            controlsContainer: document.getElementById('controls-container'),
            slackWebhookUrlInput: document.getElementById('slack-webhook-url'),
            saveWebhookBtn: document.getElementById('save-webhook-btn'),
            listenersList: document.getElementById('listeners-list'),
            listenerModal: document.getElementById('listener-modal'),
            listenerForm: document.getElementById('listener-form'),
            listenerModalTitle: document.getElementById('listener-modal-title'),
            listenerLabelInput: document.getElementById('listener-label'),
            listenerPatternInput: document.getElementById('listener-pattern'),
            originalListenerLabelInput: document.getElementById('original-listener-label'),
            showAddListenerModalBtn: document.getElementById('show-add-listener-modal-btn'),
            cancelListenerBtn: document.getElementById('cancel-listener-btn'),
            saveListenerBtn: document.getElementById('save-listener-btn'),
            listenerLabelError: document.getElementById('listener-label-error'),
            listenerPatternError: document.getElementById('listener-pattern-error'),
            regexValidityIndicator: document.getElementById('regex-validity-indicator'),
            regexTestString: document.getElementById('regex-test-string'),
            regexTestResult: document.getElementById('regex-test-result'),
            sourcesList: document.getElementById('sources-list'),
            sourceModal: document.getElementById('source-modal'),
            sourceForm: document.getElementById('source-form'),
            sourceModalTitle: document.getElementById('source-modal-title'),
            originalSourceNameInput: document.getElementById('original-source-name'),
            sourceNameInput: document.getElementById('source-name'),
            sourceApiUrlInput: document.getElementById('source-api-url'),
            sourceDataRootInput: document.getElementById('source-data-root'),
            sourceFieldsToCheckTextarea: document.getElementById('source-fields-to-check'),
            fieldsToCheckContainer: document.getElementById('fields-to-check-container'),
            sourceFieldMappingsTextarea: document.getElementById('source-field-mappings'),
            showAddSourceModalBtn: document.getElementById('show-add-source-modal-btn'),
            cancelSourceBtn: document.getElementById('cancel-source-btn'),
            saveSourceBtn: document.getElementById('save-source-btn'),
            scanAllSourcesBtn: document.getElementById('scan-all-sources-btn'),
            fetchPreviewBtn: document.getElementById('fetch-preview-btn'),
            sourcePreviewContainer: document.getElementById('source-preview-container'),
            previewStatus: document.getElementById('preview-status'),
            previewContent: document.getElementById('preview-content'),
            selectedPathDisplay: document.getElementById('selected-path-display'),
            interactivePreviewItem: document.getElementById('interactive-preview-item'),
            rawJsonPreview: document.getElementById('raw-json-preview'),
            mappingInputsContainer: document.getElementById('mapping-inputs-container'),
            quickAddToggle: document.getElementById('quick-add-toggle'),
            sidebarSourceTemplatesContainer: document.getElementById('sidebar-source-templates-container'),
        };
        let currentPatterns = [];
        let apiSources = [];
        let sourceTemplates = [];
        let eventSource = null;
        let activeScan = { sourceName: null, nextPage: 1 };
        let currentStatus = 'idle';
        let selectedJsonPath = null;
        let previewData = null;
        let currentFieldsToCheck = [];
        const requiredMappings = ["id", "title", "url", "text", "by", "time"];
        let listenerFormValidity = { label: false, pattern: false };

        const debounce = (func, delay) => {
            let timeoutId;
            return (...args) => {
                clearTimeout(timeoutId);
                timeoutId = setTimeout(() => {
                    func.apply(this, args);
                }, delay);
            };
        };

        function setupConfigControls() {
            ui.slackWebhookUrlInput.value = localStorage.getItem('slackWebhookUrl') || '';
            ui.saveWebhookBtn.addEventListener('click', () => {
                localStorage.setItem('slackWebhookUrl', ui.slackWebhookUrlInput.value.trim());
                ui.saveWebhookBtn.textContent = 'Saved!';
                setTimeout(() => { ui.saveWebhookBtn.textContent = 'Save'; }, 2000);
                updateAllSlackButtons();
            });
        }
        async function fetchData(url, setter, renderer, postRender) {
            try {
                const response = await fetch(url);
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();
                setter(data);
                renderer();
                if (postRender) postRender();
            } catch (e) {
                console.error(`Failed to fetch from ${url}:`, e);
            }
        }
        async function updateDataOnServer(url, data) {
            await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
        }
        const setPatterns = (data) => { currentPatterns = data; };
        const fetchPatterns = () => fetchData('/patterns', setPatterns, renderPatterns);
        const updatePatterns = () => updateDataOnServer('/patterns', currentPatterns);
        function renderPatterns() {
            ui.listenersList.innerHTML = currentPatterns.length === 0
                ? '<p class="text-sm text-gray-500 italic p-2 text-center">Add a listener to begin.</p>'
                : '';
            currentPatterns.forEach(p => {
                const div = document.createElement('div');
                div.className = 'flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80 transition-all shadow-md';
                div.innerHTML = `
                    <div class="flex-1 overflow-hidden">
                        <p class="font-semibold text-sm truncate" title="${p.label}">${p.label}</p>
                        <p class="text-xs text-gray-400 font-mono truncate" title="${p.pattern}">${p.pattern}</p>
                    </div>
                    <div class="flex items-center space-x-3 ml-2">
                        <button title="Edit Listener" class="edit-btn text-gray-500 hover:text-blue-400 transition-transform transform hover:scale-125" data-type="listener" data-label="${p.label}">
                            <i class="fa-solid fa-pencil"></i>
                        </button>
                        <button title="Remove Listener" class="remove-btn text-gray-500 hover:text-red-400 transition-transform transform hover:scale-125" data-type="listener" data-label="${p.label}">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </div>
                `;
                ui.listenersList.appendChild(div);
            });
        }
        const setSources = (data) => { apiSources = data; };
        const fetchSources = () => fetchData('/api-sources', setSources, renderSources);
        const updateSources = () => updateDataOnServer('/api-sources', apiSources);
        const setSourceTemplates = (data) => { sourceTemplates = data; };
        const fetchSourceTemplates = () => fetchData('/api-source-templates', setSourceTemplates, renderSourceTemplates);
        
        function getSourceControlsHTML(source) {
            const sourceName = source.name;
            const isThisSourceActive = activeScan.sourceName === sourceName;
            const isAnyScanActive = ['scanning', 'manually_paused', 'scan_paused'].includes(currentStatus);
            if (isThisSourceActive) {
                if (currentStatus === 'manually_paused') {
                    return `
                        <button title="Resume Scan" class="resume-btn p-2 text-green-400 hover:text-green-300 transition-transform transform hover:scale-125" data-name="${sourceName}">
                            <i class="fa-solid fa-play fa-lg"></i>
                        </button>
                        <button title="Stop Scan" class="stop-btn p-2 text-red-500 hover:text-red-400 transition-transform transform hover:scale-125" data-name="${sourceName}">
                            <i class="fa-solid fa-stop fa-lg"></i>
                        </button>
                    `;
                }
                if (currentStatus === 'scanning') {
                    return `
                        <i class="fa-solid fa-spinner fa-spin text-blue-400 mx-2 text-lg"></i>
                        <button title="Pause Scan" class="pause-btn p-2 text-yellow-400 hover:text-yellow-300 transition-transform transform hover:scale-125" data-name="${sourceName}">
                            <i class="fa-solid fa-pause fa-lg"></i>
                        </button>
                        <button title="Stop Scan" class="stop-btn p-2 text-red-500 hover:text-red-400 transition-transform transform hover:scale-125" data-name="${sourceName}">
                            <i class="fa-solid fa-stop fa-lg"></i>
                        </button>
                    `;
                }
            }
            const isDisabled = isAnyScanActive && !isThisSourceActive;
            const disabledAttr = isDisabled ? 'disabled' : '';
            const disabledClass = isDisabled ? 'text-gray-600 cursor-not-allowed' : 'text-green-400 hover:text-green-300 transition-transform transform hover:scale-125';
            return `
                <button title="Start Scan" class="scan-btn p-2 ${disabledClass}" data-name="${sourceName}" ${disabledAttr}>
                    <i class="fa-solid fa-play fa-lg"></i>
                </button>
            `;
        }
        function renderSources() {
            ui.sourcesList.innerHTML = apiSources.length === 0
                ? '<p class="text-sm text-gray-500 italic p-2 text-center">Add an API source to scan.</p>'
                : '';
            apiSources.forEach(source => {
                const div = document.createElement('div');
                const isScanning = activeScan.sourceName === source.name && currentStatus === 'scanning';
                div.className = `flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80 transition-all shadow-md border ${isScanning ? 'scanning-source' : 'brand-border'}`;
                const controlsHTML = getSourceControlsHTML(source);
                div.innerHTML = `
                    <div class="flex-1 overflow-hidden">
                        <p class="font-semibold text-sm truncate font-mono" title="${source.name}">${source.name}</p>
                    </div>
                    <div class="flex items-center space-x-2 ml-2">
                        <div class="scan-controls flex items-center space-x-2">${controlsHTML}</div>
                        <button title="Edit API Source" class="edit-btn text-gray-500 hover:text-blue-400 transition-transform transform hover:scale-125" data-type="source" data-name="${source.name}">
                            <i class="fa-solid fa-pencil"></i>
                        </button>
                        <button title="Remove API Source" class="remove-btn text-gray-500 hover:text-red-400 transition-transform transform hover:scale-125" data-type="source" data-name="${source.name}">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </div>
                `;
                ui.sourcesList.appendChild(div);
            });
        }
        function renderSourceTemplates() {
            const container = ui.sidebarSourceTemplatesContainer;
            container.innerHTML = '';
            sourceTemplates.forEach((template, index) => {
                const templateEl = document.createElement('div');
                templateEl.className = 'quick-add-item p-3 bg-gray-900/50 border brand-border rounded-lg';
                templateEl.style.animationDelay = `${index * 100}ms`;
                
                const variablesHTML = template.variables.map(v => `
                    <div class="flex-1">
                        <label class="block text-xs font-medium text-gray-400">${v.name}</label>
                        <input type="text" data-key="${v.key}" placeholder="${v.placeholder}" 
                            class="mt-1 w-full p-1.5 text-sm rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-1 focus:ring-indigo-500">
                    </div>
                `).join('');

                templateEl.innerHTML = `
                    <p class="font-semibold text-sm">${template.name}</p>
                    <p class="text-xs text-gray-400 mb-2">${template.description}</p>
                    <div class="flex items-end gap-2">
                        ${variablesHTML}
                        <button type="button" class="apply-template-btn h-[35px] px-3 py-1 text-sm font-semibold rounded-md bg-indigo-700 hover:bg-indigo-600 transition-all shadow text-white whitespace-nowrap" data-template-id="${template.id}">
                            Apply
                        </button>
                    </div>
                `;
                container.appendChild(templateEl);
            });
        }

        function testRegexLocally() {
            const patternStr = ui.listenerPatternInput.value;
            const testStr = ui.regexTestString.value;
            const resultEl = ui.regexTestResult;

            if (!patternStr || !testStr) {
                resultEl.innerHTML = '<span class="text-gray-500">Enter a pattern and test string.</span>';
                return;
            }

            try {
                let cleanPattern = patternStr;
                let flags = 'g';

                if (patternStr.startsWith('(?i)')) {
                    cleanPattern = patternStr.substring(4);
                    flags += 'i';
                }
                
                const regex = new RegExp(cleanPattern, flags);
                const highlighted = testStr.replace(regex, (match) => `<span class="regex-match">${match}</span>`);
                
                if (highlighted !== testStr) {
                    resultEl.innerHTML = highlighted;
                } else {
                    resultEl.innerHTML = '<span class="text-gray-500">No matches found.</span>';
                }
            } catch (e) {
                resultEl.innerHTML = `<span class="text-yellow-500">Invalid JavaScript Regex: ${e.message}</span>`;
            }
        }

        const validateRegexOnServer = debounce(async () => {
            const pattern = ui.listenerPatternInput.value;
            const errorEl = ui.listenerPatternError;
            const indicatorEl = ui.regexValidityIndicator;

            if (!pattern) {
                errorEl.textContent = 'Pattern cannot be empty.';
                indicatorEl.innerHTML = '<i class="fas fa-times-circle text-red-500"></i>';
                listenerFormValidity.pattern = false;
                updateSaveButtonState();
                return;
            }

            try {
                const response = await fetch('/validate-regex', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pattern })
                });
                const result = await response.json();
                if (result.valid) {
                    errorEl.textContent = '';
                    indicatorEl.innerHTML = '<i class="fas fa-check-circle text-green-500"></i>';
                    listenerFormValidity.pattern = true;
                } else {
                    errorEl.textContent = result.error;
                    indicatorEl.innerHTML = '<i class="fas fa-times-circle text-red-500"></i>';
                    listenerFormValidity.pattern = false;
                }
            } catch (e) {
                errorEl.textContent = 'Could not reach validation server.';
                indicatorEl.innerHTML = '<i class="fas fa-exclamation-triangle text-yellow-500"></i>';
                listenerFormValidity.pattern = false;
            }
            updateSaveButtonState();
        }, 300);

        function validateLabel() {
            const newLabel = ui.listenerLabelInput.value.trim();
            const originalLabel = ui.originalListenerLabelInput.value;
            const errorEl = ui.listenerLabelError;
            
            if (!newLabel) {
                errorEl.textContent = 'Label cannot be empty.';
                listenerFormValidity.label = false;
                updateSaveButtonState();
                return;
            }

            const isEditing = !!originalLabel;
            const isDuplicate = currentPatterns.some(p => p.label === newLabel) && (!isEditing || newLabel !== originalLabel);

            if (isDuplicate) {
                errorEl.textContent = 'This label is already in use.';
                listenerFormValidity.label = false;
            } else {
                errorEl.textContent = '';
                listenerFormValidity.label = true;
            }
            updateSaveButtonState();
        }

        function updateSaveButtonState() {
            ui.saveListenerBtn.disabled = !(listenerFormValidity.label && listenerFormValidity.pattern);
        }
        
        function setupManagementEventListeners() {
            ui.showAddListenerModalBtn.addEventListener('click', () => openModal('listener'));
            ui.showAddSourceModalBtn.addEventListener('click', () => openModal('source'));
            ui.cancelListenerBtn.addEventListener('click', () => closeModal('listener'));
            ui.cancelSourceBtn.addEventListener('click', () => closeModal('source'));
            ui.listenerModal.addEventListener('click', (e) => { if (e.target === ui.listenerModal) closeModal('listener'); });
            ui.sourceModal.addEventListener('click', (e) => { if (e.target === ui.sourceModal) closeModal('source'); });
            ui.listenerForm.addEventListener('submit', handleSave);
            ui.saveSourceBtn.addEventListener('click', () => handleSave({ target: ui.sourceForm }));
            document.body.addEventListener('click', e => {
                const btn = e.target.closest('.edit-btn, .remove-btn');
                if (btn) {
                    const { type, ...data } = btn.dataset;
                    if (btn.classList.contains('edit-btn')) openModal(type, data);
                    else handleRemove(type, data);
                    return;
                }
                const applyBtn = e.target.closest('.apply-template-btn');
                if(applyBtn) {
                    handleApplyTemplate(applyBtn);
                    return;
                }
            });
            ui.sourcesList.addEventListener('click', (e) => {
                const scanBtn = e.target.closest('.scan-btn');
                const pauseBtn = e.target.closest('.pause-btn');
                const resumeBtn = e.target.closest('.resume-btn');
                const stopBtn = e.target.closest('.stop-btn');
                if (scanBtn) startScan(scanBtn.dataset.name, 1);
                if (pauseBtn) handlePauseScan();
                if (resumeBtn) handleResumeScan();
                if (stopBtn) handleStopScan();
            });
            ui.scanAllSourcesBtn.addEventListener('click', scanAllSources);
            ui.feedContainer.addEventListener('click', handleFeedActions);
            ui.fetchPreviewBtn.addEventListener('click', handleFetchPreview);
            ui.sourceModal.addEventListener('click', e => {
                if(e.target.matches('.preview-tab-btn')) handleTabSwitch(e.target);
                if(e.target.closest('.json-value')) handleJsonItemClick(e.target.closest('.json-value'));
                if(e.target.closest('.mapping-target-btn')) handleMappingTargetClick(e.target.closest('.mapping-target-btn'));
            });
            ui.sourceDataRootInput.addEventListener('input', updateInteractivePreview);
            ui.fieldsToCheckContainer.addEventListener('click', e => {
                if (e.target.closest('.remove-field-btn')) {
                    const field = e.target.closest('.remove-field-btn').dataset.field;
                    currentFieldsToCheck = currentFieldsToCheck.filter(f => f !== field);
                    renderFieldsToCheck();
                    updateInteractivePreview(); // Sync checkboxes
                }
            });

            // Event listener for checkboxes in the interactive preview
            ui.interactivePreviewItem.addEventListener('change', e => {
                if (e.target.matches('.json-checkbox')) {
                    const path = e.target.dataset.path;
                    if (e.target.checked) {
                        if (!currentFieldsToCheck.includes(path)) {
                            currentFieldsToCheck.push(path);
                        }
                    } else {
                        currentFieldsToCheck = currentFieldsToCheck.filter(p => p !== path);
                    }
                    renderFieldsToCheck();
                }
            });

            // Listener modal live validation
            ui.listenerLabelInput.addEventListener('input', validateLabel);
            ui.listenerPatternInput.addEventListener('input', () => {
                validateRegexOnServer();
                testRegexLocally();
            });
            ui.regexTestString.addEventListener('input', testRegexLocally);
            
            // Accordion for Quick Add
            ui.quickAddToggle.addEventListener('click', () => {
                ui.quickAddToggle.classList.toggle('active');
                ui.sidebarSourceTemplatesContainer.classList.toggle('active');
            });
        }
        function openModal(type, data = null) {
            const isEdit = data !== null;
            const modal = ui[`${type}Modal`];
            const modalContent = modal.querySelector('.modal-content');
            const form = ui[`${type}Form`];
            form.reset();
            ui[`${type}ModalTitle`].textContent = `${isEdit ? 'Edit' : 'Add New'} ${type === 'listener' ? 'Listener' : 'API Source'}`;
            if (type === 'listener') {
                ui.listenerLabelError.textContent = '';
                ui.listenerPatternError.textContent = '';
                ui.regexValidityIndicator.innerHTML = '';
                ui.regexTestString.value = '';
                ui.regexTestResult.innerHTML = '<span class="text-gray-500">Enter a pattern and test string.</span>';
                
                ui.originalListenerLabelInput.value = isEdit ? data.label : '';
                if (isEdit) {
                    const p = currentPatterns.find(p => p.label === data.label);
                    ui.listenerLabelInput.value = p.label;
                    ui.listenerPatternInput.value = p.pattern;
                }
                validateLabel();
                validateRegexOnServer.flush ? validateRegexOnServer.flush() : validateRegexOnServer();
                testRegexLocally();
            } else if (type === 'source') {
                resetPreviewer();
                renderMappingInputs();
                ui.originalSourceNameInput.value = isEdit ? data.name : '';
                if (isEdit) {
                    const s = apiSources.find(s => s.name === data.name);
                    ui.sourceNameInput.value = s.name;
                    ui.sourceApiUrlInput.value = s.apiUrl;
                    ui.sourceDataRootInput.value = s.dataRoot || '';
                    currentFieldsToCheck = s.fieldsToCheck || [];
                    ui.sourceFieldMappingsTextarea.value = JSON.stringify(s.fieldMappings || {}, null, 2);
                } else {
                    const defaultMappings = {};
                    requiredMappings.forEach(k => defaultMappings[k] = "");
                    ui.sourceFieldMappingsTextarea.value = JSON.stringify(defaultMappings, null, 2);
                    currentFieldsToCheck = [];
                }
                renderFieldsToCheck();
                populateMappingsFromTextarea();
            }
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modalContent.classList.remove('scale-95');
            }, 10);
        }
        function closeModal(type) {
            const modal = ui[`${type}Modal`];
            const modalContent = modal.querySelector('.modal-content');
            modal.classList.add('opacity-0');
            modalContent.classList.add('scale-95');
            setTimeout(() => modal.classList.add('hidden'), 300);
        }
        function handleSave(e) {
            e.preventDefault();
            const type = e.target.id.split('-')[0];
            if (type === 'listener') {
                const newLabel = ui.listenerLabelInput.value.trim();
                const originalLabel = ui.originalListenerLabelInput.value;
                const isEditing = !!originalLabel;

                const patternData = { label: newLabel, pattern: ui.listenerPatternInput.value.trim() };
                if (isEditing) {
                    currentPatterns = currentPatterns.map(p => p.label === originalLabel ? patternData : p);
                } else {
                    currentPatterns.push(patternData);
                }
                renderPatterns();
                updatePatterns();
            } else if (type === 'source') {
                syncMappingsToTextarea();
                syncFieldsToCheckToTextarea();
                const newName = ui.sourceNameInput.value.trim();
                const originalName = ui.originalSourceNameInput.value;
                const isEditing = !!originalName;
                if (apiSources.some(s => s.name === newName) && (!isEditing || newName !== originalName)) {
                    return alert("An API source with that name already exists.");
                }
                try {
                    const sourceData = {
                        name: newName,
                        apiUrl: ui.sourceApiUrlInput.value.trim(),
                        httpMethod: "GET", // Assuming GET for now
                        paginationStyle: "page_number", // Assuming page_number
                        dataRoot: ui.sourceDataRootInput.value.trim(),
                        fieldsToCheck: ui.sourceFieldsToCheckTextarea.value.split('\\n').map(f => f.trim()).filter(Boolean),
                        fieldMappings: JSON.parse(ui.sourceFieldMappingsTextarea.value)
                    };
                    if (isEditing) {
                        apiSources = apiSources.map(s => s.name === originalName ? sourceData : s);
                    } else {
                        apiSources.push(sourceData);
                    }
                    renderSources();
                    updateSources();
                } catch (err) {
                    return alert("Invalid JSON in Field Mappings. Please check the format.");
                }
            }
            closeModal(type);
        }
        function handleRemove(type, data) {
            if (type === 'listener') {
                currentPatterns = currentPatterns.filter(p => p.label !== data.label);
                renderPatterns(); updatePatterns();
            } else if (type === 'source') {
                apiSources = apiSources.filter(s => s.name !== data.name);
                renderSources(); updateSources();
            }
        }
        function createFeedCard(item, delay = 0) {
            const card = document.createElement('div');
            const isPending = item.summary_status === 'pending';
            card.className = 'feed-card brand-dark-bg border brand-border rounded-lg p-5 shadow-lg card-enter-animation opacity-0';
            card.style.animationDelay = `${delay}ms`;
            if (isPending) card.classList.add('summary-pending');
            card.dataset.itemId = item.id;
            card.dataset.itemData = JSON.stringify(item);
            const postTime = new Date(item.time * 1000).toLocaleString('en-US', { timeZone: 'UTC' });
            const webhookUrl = localStorage.getItem('slackWebhookUrl') || '';
            const titleLink = item.url
                ? `<a href="${item.url}" target="_blank" class="text-xl font-bold text-white hover:text-green-400 transition-colors mb-2 sm:mb-0 break-all">${item.title}</a>`
                : `<span class="text-xl font-bold text-white mb-2 sm:mb-0 break-all">${item.title}</span>`;
            const viewSourceLink = item.url
                ? `<a href="${item.url}" target="_blank" class="hover:text-green-400 transition-colors"><i class="fa-solid fa-arrow-up-right-from-square mr-1"></i> View Source</a>`
                : `<span><i class="fa-solid fa-database mr-1"></i> ${item.source_name}</span>`;
            const summaryHTML = isPending
                ? `<div class="summary-content text-gray-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Generating AI Summary...</div>`
                : `<div class="summary-content markdown-content text-gray-300 text-sm">${marked.parse(item.ai_summary || '')}</div>`;
            card.innerHTML = `
                <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-2">
                    ${titleLink}
                    <span class="text-xs font-mono text-white bg-blue-900/70 border border-blue-700 px-2 py-1 rounded-full w-max shrink-0 shadow-md">
                        <i class="fa-solid fa-tag mr-1"></i> ${item.matched_label}
                    </span>
                </div>
                <div class="flex items-center flex-wrap gap-x-4 gap-y-2 text-xs text-gray-400 mt-2 border-b border-gray-700 pb-3 mb-3">
                    <span class="inline-flex items-center text-xs font-medium text-purple-300 bg-purple-900/50 border border-purple-700 px-2 py-0.5 rounded-full shadow">
                        <i class="fa-solid fa-satellite-dish mr-1.5"></i> ${item.source_name}
                    </span>
                    <span><i class="fa-solid fa-user mr-1"></i> ${item.by || 'N/A'}</span>
                    <span><i class="fa-solid fa-clock mr-1"></i> ${postTime} UTC</span>
                    ${viewSourceLink}
                    <button class="send-to-slack-btn text-gray-400 hover:text-white text-xs disabled:opacity-50 disabled:cursor-not-allowed transition-colors" ${!webhookUrl || isPending ? 'disabled' : ''} title="${!webhookUrl ? 'Enter a Slack Webhook URL' : isPending ? 'Summary not ready' : 'Send to Slack'}">
                        <i class="fa-brands fa-slack mr-1"></i> Send to Slack
                    </button>
                </div>
                <div class="summary-container">
                    <h3 class="text-sm font-semibold text-green-400 mb-2">AI Summary</h3>
                    ${summaryHTML}
                </div>
            `;
            return card;
        }
        function updateAllSlackButtons() {
            const url = localStorage.getItem('slackWebhookUrl') || '';
            document.querySelectorAll('.feed-card .send-to-slack-btn').forEach(btn => {
                const card = btn.closest('.feed-card');
                const isPending = card.classList.contains('summary-pending');
                btn.disabled = !url || isPending;
                btn.title = !url ? 'Enter a Slack Webhook URL' : isPending ? 'Summary not ready' : 'Send to Slack';
            });
        }
        function setStatus(status, text) {
            ui.statusText.textContent = text;
            ui.statusDot.className = 'w-3 h-3 rounded-full transition-all';
            const statusClasses = {
                scanning: 'bg-yellow-500 animate-pulse',
                scan_paused: 'bg-yellow-500',
                manually_paused: 'bg-yellow-500',
                rate_limit_paused: 'bg-red-500 animate-pulse',
                error: 'bg-red-500',
                idle: 'bg-gray-500'
            };
            ui.statusDot.classList.add(...(statusClasses[status] || statusClasses.idle).split(' '));
        }
        function handleStatusUpdate(data) {
            currentStatus = data.status;
            setStatus(data.status, data.reason || data.status);
            if (data.source_name) activeScan.sourceName = data.source_name;
            if (data.next_page) activeScan.nextPage = data.next_page;
            if (['idle', 'error'].includes(data.status)) {
                activeScan.sourceName = null;
            }
            renderSources();
            updateControlsUI(data);
            if (data.status === 'manually_paused') {
                document.querySelectorAll('.feed-card.summary-pending').forEach(card => {
                    const summaryContent = card.querySelector('.summary-content');
                    if (summaryContent && summaryContent.innerHTML.includes('fa-spinner')) {
                        summaryContent.innerHTML = `
                            <div class="flex items-center gap-4">
                                <p class="text-yellow-400">Summary generation paused.</p>
                                <button class="generate-summary-btn px-2 py-1 text-xs font-semibold rounded bg-green-800 hover:bg-green-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">Generate</button>
                            </div>`;
                    }
                });
            }
        }
        function updateControlsUI(data = {}) {
            const controls = ui.controlsContainer;
            const status = data.status || currentStatus;
            switch (status) {
                case 'scanning':
                    controls.innerHTML = `
                        <div class="sauron-eye-container">
                            <div class="sauron-eye"></div>
                            <div class="scan-beam"></div>
                        </div>
                        <p class="text-sm text-gray-400 mt-4">${data.reason || 'Scanning...'}</p>`;
                    break;
                case 'scan_paused':
                    controls.innerHTML = `
                        <p class="text-lg font-semibold text-yellow-400 mb-3">${data.reason}</p>
                        <div class="flex items-center gap-4">
                            <button id="continue-scan-btn" class="px-6 py-2 font-semibold rounded-md bg-blue-700 hover:bg-blue-600 transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"><i class="fa-solid fa-forward mr-2"></i>Continue Scan</button>
                            <button id="stop-btn" class="px-5 py-2 font-semibold rounded-md bg-red-700 hover:bg-red-600 transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"><i class="fa-solid fa-stop mr-2"></i>Stop</button>
                        </div>`;
                    document.getElementById('continue-scan-btn').addEventListener('click', () => startScan(activeScan.sourceName, activeScan.nextPage));
                    document.getElementById('stop-btn').addEventListener('click', handleStopScan);
                    break;
                case 'rate_limit_paused':
                    controls.innerHTML = `
                        <p class="text-red-400 font-semibold mb-3">${data.reason}</p>
                        <button id="rate-limit-resume-btn" class="px-5 py-2.5 font-semibold rounded-md bg-green-700 hover:bg-green-600 transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"><i class="fa-solid fa-play mr-2"></i>Resume Operations</button>`;
                    document.getElementById('rate-limit-resume-btn').addEventListener('click', handleResumeOperations);
                    break;
                default:
                    controls.innerHTML = '';
                    break;
            }
        }
        async function handleControlAction(endpoint) {
            await fetch(endpoint, { method: 'POST' });
        }
        const handlePauseScan = () => handleControlAction('/pause-scan');
        const handleResumeScan = () => handleControlAction('/resume-scan');
        const handleResumeOperations = () => handleControlAction('/resume-operations');
        async function handleStopScan() {
            await handleControlAction('/cancel-scan');
            handleStatusUpdate({status: 'idle', reason: 'Scan cancelled.'});
        }
        async function handleFeedActions(e) {
            const sendBtn = e.target.closest('.send-to-slack-btn');
            const generateBtn = e.target.closest('.generate-summary-btn');
            if (sendBtn) await handleSendToSlack(sendBtn);
            if (generateBtn) await handleGenerateSummary(generateBtn);
        }
        async function handleGenerateSummary(btn) {
            const card = btn.closest('.feed-card');
            const itemData = JSON.parse(card.dataset.itemData);
            const summaryContent = card.querySelector('.summary-content');
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            try {
                const response = await fetch('/generate-summary', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(itemData)
                });
                if (!response.ok) throw new Error('Failed to generate summary.');
                const result = await response.json();
                summaryContent.innerHTML = `<div class="summary-content markdown-content text-gray-300 text-sm">${marked.parse(result.ai_summary)}</div>`;
                card.classList.remove('summary-pending');
                const updatedItemData = { ...itemData, ai_summary: result.ai_summary };
                card.dataset.itemData = JSON.stringify(updatedItemData);
                updateAllSlackButtons();
            } catch (error) {
                summaryContent.innerHTML = `<p class="text-red-400">Error: ${error.message}</p>`;
                btn.innerHTML = 'Retry';
                btn.disabled = false;
            }
        }
        async function handleSendToSlack(btn) {
            const itemData = JSON.parse(btn.closest('.feed-card').dataset.itemData);
            const webhookUrl = localStorage.getItem('slackWebhookUrl') || '';
            if (!webhookUrl) return;
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Sending...';
            try {
                const response = await fetch('/send-to-slack', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item: itemData, webhookUrl })
                });
                if (!response.ok) {
                    const errData = await response.json();
                    throw new Error(errData.error || 'Unknown error');
                }
                btn.innerHTML = '<i class="fa-solid fa-check mr-1"></i> Sent!';
                btn.classList.add('text-green-400');
            } catch (error) {
                btn.innerHTML = '<i class="fa-solid fa-xmark mr-1"></i> Failed';
                btn.classList.add('text-red-400');
                setTimeout(() => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-brands fa-slack mr-1"></i> Send to Slack';
                    btn.classList.remove('text-red-400');
                }, 3000);
            }
        }
        
        // --- API Previewer & Mapper Functions ---
        async function handleApplyTemplate(button) {
            const templateId = button.dataset.templateId;
            const template = sourceTemplates.find(t => t.id === templateId);
            if (!template) return;

            const container = button.closest('.quick-add-item');
            const replacements = {};
            let allVarsFilled = true;
            
            template.variables.forEach(v => {
                const input = container.querySelector(`input[data-key="${v.key}"]`);
                if (input && input.value.trim()) {
                    replacements[v.key] = input.value.trim();
                } else {
                    allVarsFilled = false;
                }
            });

            if (!allVarsFilled) {
                alert('Please fill in all template fields.');
                return;
            }

            // Calculate final config
            let finalApiUrl = template.config.apiUrl;
            let finalName = template.config.name;
            for (const [key, value] of Object.entries(replacements)) {
                finalApiUrl = finalApiUrl.replace(new RegExp(key.replace(/\\{/g, '\\{').replace(/\\}/g, '\\}'), 'g'), value);
                finalName = finalName.replace(new RegExp(key.replace(/\\{/g, '\\{').replace(/\\}/g, '\\}'), 'g'), value);
            }

            openModal('source');

            // Populate the modal form with the template data
            ui.sourceNameInput.value = finalName;
            ui.sourceApiUrlInput.value = finalApiUrl;
            ui.sourceDataRootInput.value = template.config.dataRoot || '';
            currentFieldsToCheck = [...template.config.fieldsToCheck] || [];
            renderFieldsToCheck();
            ui.sourceFieldMappingsTextarea.value = JSON.stringify(template.config.fieldMappings || {}, null, 2);
            populateMappingsFromTextarea();
            
            // Collapse the sidebar section
            ui.quickAddToggle.classList.remove('active');
            ui.sidebarSourceTemplatesContainer.classList.remove('active');

            // Automatically trigger the preview after the modal animation
            setTimeout(() => {
                handleFetchPreview();
            }, 350);
        }

        function resetPreviewer() {
            previewData = null;
            selectedJsonPath = null;
            document.body.classList.remove('path-selected');
            ui.sourcePreviewContainer.classList.add('hidden');
            ui.previewContent.classList.add('hidden');
            ui.previewStatus.classList.remove('hidden');
            ui.previewStatus.innerHTML = 'Click "Preview" to load sample data from your API.';
            ui.selectedPathDisplay.textContent = '';
        }

        async function handleFetchPreview() {
            const apiUrl = ui.sourceApiUrlInput.value.trim();
            if (!apiUrl) {
                alert("Please enter an API URL first.");
                return;
            }
            resetPreviewer();
            ui.sourcePreviewContainer.classList.remove('hidden');
            ui.previewStatus.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>Fetching data...';
            ui.fetchPreviewBtn.disabled = true;

            try {
                const response = await fetch('/preview-api-source', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ apiUrl })
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Unknown error fetching preview.');
                }
                previewData = data;
                ui.previewStatus.classList.add('hidden');
                ui.previewContent.classList.remove('hidden');
                ui.rawJsonPreview.textContent = JSON.stringify(previewData, null, 2);
                updateInteractivePreview();
            } catch (e) {
                ui.previewStatus.innerHTML = `<span class="text-red-400"><i class="fa-solid fa-triangle-exclamation mr-2"></i>Error: ${e.message}</span>`;
            } finally {
                ui.fetchPreviewBtn.disabled = false;
            }
        }

        function getNestedValue(obj, path) {
            if (!path) return obj;
            return path.split('.').reduce((acc, part) => acc && acc[part], obj);
        }

        function updateInteractivePreview() {
            if (!previewData) return;
            const dataRoot = ui.sourceDataRootInput.value.trim();
            const items = getNestedValue(previewData, dataRoot);
            
            if (Array.isArray(items) && items.length > 0) {
                renderInteractiveJson(items[0], ui.interactivePreviewItem, dataRoot ? `${dataRoot}.0` : '0');
                ui.sourceDataRootInput.classList.remove('border-red-500');
                ui.sourceDataRootInput.classList.add('border-green-500');
            } else {
                ui.interactivePreviewItem.innerHTML = `<span class="text-yellow-400">Could not find an array of items at data root '${dataRoot}'. Displaying the full response.</span>`;
                renderInteractiveJson(previewData, ui.interactivePreviewItem, '');
                if (dataRoot) {
                    ui.sourceDataRootInput.classList.add('border-red-500');
                    ui.sourceDataRootInput.classList.remove('border-green-500');
                }
            }
        }

        function renderInteractiveJson(obj, container, pathPrefix = '') {
            container.innerHTML = '';
            
            const getRelativePath = (fullPath) => {
                const dataRoot = ui.sourceDataRootInput.value.trim();
                let relativePath = fullPath;
                if (dataRoot) {
                    const prefix = dataRoot + '.0.';
                    if (fullPath.startsWith(prefix)) {
                        relativePath = fullPath.substring(prefix.length);
                    }
                } else if (fullPath.startsWith('0.')) {
                        relativePath = fullPath.substring(2);
                }
                return relativePath;
            };

            const createNode = (key, value, path) => {
                const isObject = typeof value === 'object' && value !== null;
                const entry = document.createElement('div');
                const pathParts = pathPrefix ? path.replace(pathPrefix, '').split('.') : path.split('.');
                const paddingDepth = Math.max(0, pathParts.length - 1);
                entry.style.paddingLeft = `${paddingDepth}rem`;
                
                const keySpan = `<span class="json-key">"${key}": </span>`;
                
                if (isObject) {
                    entry.innerHTML = keySpan + (Array.isArray(value) ? '[' : '{');
                    container.appendChild(entry);
                    Object.entries(value).forEach(([k, v]) => createNode(k, v, path ? `${path}.${k}` : k));
                    const closingEntry = document.createElement('div');
                    closingEntry.style.paddingLeft = entry.style.paddingLeft;
                    closingEntry.innerHTML = Array.isArray(value) ? ']' : '}';
                    container.appendChild(closingEntry);
                } else {
                    const relativePath = getRelativePath(path);
                    const isChecked = currentFieldsToCheck.includes(relativePath);
                    const type = typeof value === 'string' ? 'string' : typeof value === 'number' ? 'number' : 'boolean';
                    const displayValue = typeof value === 'string' ? `"${value}"` : value;
                    entry.innerHTML = `
                        <label class="json-entry-label">
                            <input type="checkbox" class="json-checkbox" data-path="${relativePath}" ${isChecked ? 'checked' : ''}>
                            ${keySpan}<span class="json-value json-${type}" data-path="${path}">${displayValue}</span>
                        </label>
                    `;
                    container.appendChild(entry);
                }
            };
            Object.entries(obj).forEach(([key, value]) => createNode(key, value, pathPrefix ? `${pathPrefix}.${key}` : key));
        }

        function handleTabSwitch(target) {
            document.querySelectorAll('.preview-tab-btn').forEach(btn => btn.classList.remove('active-tab'));
            target.classList.add('active-tab');
            document.querySelectorAll('.preview-tab-content').forEach(content => content.classList.add('hidden'));
            document.getElementById(`preview-${target.dataset.tab}-tab`).classList.remove('hidden');
        }

        function handleJsonItemClick(item) {
            document.querySelectorAll('.selected-json-path').forEach(el => el.classList.remove('selected-json-path'));
            item.classList.add('selected-json-path');
            const fullPath = item.dataset.path;
            const dataRoot = ui.sourceDataRootInput.value.trim();
            let relativePath = fullPath;
            if (dataRoot) {
                const prefix = dataRoot + '.0.';
                if (fullPath.startsWith(prefix)) {
                    relativePath = fullPath.substring(prefix.length);
                }
            } else if (fullPath.startsWith('0.')) {
                relativePath = fullPath.substring(2);
            }
            selectedJsonPath = relativePath;
            ui.selectedPathDisplay.textContent = `Selected: ${selectedJsonPath}`;
            document.body.classList.add('path-selected');
        }

        function renderMappingInputs() {
            ui.mappingInputsContainer.innerHTML = '';
            requiredMappings.forEach(key => {
                const div = document.createElement('div');
                div.className = 'flex items-center gap-2';
                div.innerHTML = `
                    <label class="w-16 text-sm text-gray-400 font-mono shrink-0">${key}:</label>
                    <input type="text" readonly data-key="${key}" placeholder="<not mapped>" class="flex-grow p-2 rounded-md bg-gray-900 border brand-border focus:outline-none text-sm font-mono text-gray-300 transition-all duration-300">
                    <button type="button" data-key="${key}" title="Map selected path to '${key}'" class="mapping-target-btn px-3 py-2 text-lg rounded-md bg-gray-700 hover:bg-blue-700 text-blue-400 hover:text-white transition-all">
                        <i class="fa-solid fa-crosshairs pointer-events-none"></i>
                    </button>
                `;
                ui.mappingInputsContainer.appendChild(div);
            });
        }
        
        function syncMappingsToTextarea() {
            const mappings = {};
            ui.mappingInputsContainer.querySelectorAll('input[data-key]').forEach(input => {
                mappings[input.dataset.key] = input.value.trim();
            });
            ui.sourceFieldMappingsTextarea.value = JSON.stringify(mappings, null, 2);
        }
        
        function populateMappingsFromTextarea() {
            try {
                const mappings = JSON.parse(ui.sourceFieldMappingsTextarea.value);
                Object.entries(mappings).forEach(([key, value]) => {
                    const input = ui.mappingInputsContainer.querySelector(`input[data-key="${key}"]`);
                    if (input) {
                        input.value = value;
                    }
                });
            } catch (e) {
                console.warn("Could not parse initial field mappings.", e);
            }
        }
        
        function handleMappingTargetClick(target) {
            if (!selectedJsonPath) {
                ui.selectedPathDisplay.textContent = 'Select a value from the preview first!';
                setTimeout(() => { ui.selectedPathDisplay.textContent = selectedJsonPath || '' }, 2000);
                return;
            }
            const key = target.dataset.key;
            const input = ui.mappingInputsContainer.querySelector(`input[data-key="${key}"]`);
            if (input) {
                input.value = selectedJsonPath;
                syncMappingsToTextarea();
                
                ui.selectedPathDisplay.textContent = `Mapped '${key}'!`;
                document.querySelectorAll('.selected-json-path').forEach(el => el.classList.remove('selected-json-path'));
                document.body.classList.remove('path-selected');
                
                input.classList.add('bg-green-900/50', 'border-green-500');
                setTimeout(() => {
                    input.classList.remove('bg-green-900/50', 'border-green-500');
                    ui.selectedPathDisplay.textContent = '';
                }, 1500);

                selectedJsonPath = null;
            }
        }

        function renderFieldsToCheck() {
            ui.fieldsToCheckContainer.innerHTML = '';
            if (currentFieldsToCheck.length === 0) {
                ui.fieldsToCheckContainer.innerHTML = '<span class="text-xs text-gray-500 p-1">Use the previewer to select fields to check.</span>';
            } else {
                currentFieldsToCheck.forEach(field => {
                    const pill = document.createElement('div');
                    pill.className = 'flex items-center gap-2 bg-gray-900/70 border brand-border rounded-full px-3 py-1 text-sm font-mono';
                    pill.innerHTML = `
                        <span>${field}</span>
                        <button type="button" class="remove-field-btn text-gray-500 hover:text-red-400" data-field="${field}" title="Remove field">
                            <i class="fa-solid fa-times-circle"></i>
                        </button>
                    `;
                    ui.fieldsToCheckContainer.appendChild(pill);
                });
            }
            syncFieldsToCheckToTextarea();
        }

        function syncFieldsToCheckToTextarea() {
            ui.sourceFieldsToCheckTextarea.value = currentFieldsToCheck.join('\\n');
        }

        // --- End of API Previewer Functions ---
        
        function connectToStream() {
            if (eventSource) return;
            eventSource = new EventSource('/stream');
            let cardAnimationDelay = 0;
            eventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);
                switch(data.type) {
                    case 'status':
                        handleStatusUpdate(data);
                        if (data.status !== 'scanning') cardAnimationDelay = 0;
                        break;
                    case 'api_item':
                        ui.placeholder.classList.add('hidden');
                        if (!document.querySelector(`[data-item-id="${data.id}"]`)) {
                            const card = createFeedCard(data, cardAnimationDelay);
                            ui.feedContainer.insertBefore(card, ui.controlsContainer.nextSibling);
                            cardAnimationDelay += 100; // Stagger animation
                        }
                        break;
                    case 'summary_update':
                        const card = document.querySelector(`[data-item-id="${data.id}"]`);
                        if (card) {
                            const summaryContent = card.querySelector('.summary-content');
                            summaryContent.innerHTML = `<div class="markdown-content text-gray-300 text-sm">${marked.parse(data.ai_summary)}</div>`;
                            card.classList.remove('summary-pending');
                            const currentData = JSON.parse(card.dataset.itemData);
                            card.dataset.itemData = JSON.stringify({ ...currentData, ai_summary: data.ai_summary });
                            updateAllSlackButtons();
                        }
                        break;
                }
            };
            eventSource.onerror = () => {
                handleStatusUpdate({status: 'error', reason: 'Connection to server lost. Reconnecting...'});
                eventSource.close();
                eventSource = null;
                setTimeout(connectToStream, 5000);
            };
        }
        async function startScan(sourceName, startPage = 1) {
            if (startPage === 1) {
                // document.querySelectorAll('.feed-card').forEach(card => card.remove());
                // ui.placeholder.classList.remove('hidden');
            }
            await fetch('/scan-source', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source_name: sourceName, start_page: startPage })
            });
        }
        function scanAllSources() {
            if (!apiSources || apiSources.length === 0) {
                return alert('No sources configured to scan.');
            }
            apiSources.forEach(s => startScan(s.name, 1));
        }
        async function initialize() {
            setupConfigControls();
            setupManagementEventListeners();
            await Promise.all([
                fetchPatterns(), 
                fetchSources(),
                fetchSourceTemplates()
            ]);
            connectToStream();
            updateControlsUI();
        }
        initialize();
    });
    </script>
</body></html>
"""

if __name__ == '__main__':
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', 5001))

    try:
        initial_patterns = json.loads(SEARCH_PATTERNS_JSON)
        update_search_patterns(initial_patterns)
    except (json.JSONDecodeError, KeyError) as e:
        logging.error(f"FATAL: Invalid format for SEARCH_PATTERNS. Using empty list. Error: {e}")
        update_search_patterns([])

    try:
        initial_sources = json.loads(DEFAULT_API_SOURCES)
        update_api_sources(initial_sources)
    except json.JSONDecodeError as e:
        logging.error(f"FATAL: Invalid format for DEFAULT_API_SOURCES. Using empty list. Error: {e}")
        update_api_sources([])

    print("--- The-Eye-Of-Sauron 👁️  (Generic API Scanner) ---")
    print(f"🚀 Starting server at http://{host}:{port}")
    print("👉 Open the URL in your browser to get started!")
    print("--------------------------------------------------")
    app.run(host=host, port=port, debug=False, threaded=True)
