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

# --- Initialization & Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

# --- Flask App Initialization ---
app = Flask(__name__)
update_queue = queue.Queue()
is_paused_due_to_rate_limit = threading.Event()

# --- Keyword Monitoring Configuration ---
DEFAULT_PATTERNS = json.dumps([
    {"pattern": "(?i)mongodb", "label": "MongoDB"},
    {"pattern": "(?i)vector search", "label": "Vector Search"},
    {"pattern": "(?i)voyageai", "label": "VoyageAI"},
])
SEARCH_PATTERNS_JSON = os.getenv("SEARCH_PATTERNS", DEFAULT_PATTERNS)
SEARCH_PATTERNS = {}
patterns_lock = threading.Lock()

# --- Service Configuration ---
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 25))
MAX_RESULTS_PER_RUN = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "application/vnd.github.v3+json"
}

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
CONTENT_SUMMARY_SYSTEM_PROMPT = """You are an expert AI analyst. Your task is to analyze the provided GitHub issue content and provide a concise, insightful summary for a business and technical audience.

**Core Directives:**
- **Summarize Key Points:** Distill the main problem, user request, or key discussion points.
- **Ignore Boilerplate:** Disregard markdown formatting, code blocks unless they are essential to the summary, and irrelevant text.
- **Format for Clarity:** Your final output must be a single, well-written paragraph of no more than 150 words.
- **Be Objective:** Do not add personal opinions, disclaimers, or apologies.
- **Ground Your Answer:** Base your summary *only* on the provided text.
- **Identify Relevance:** Briefly mention why this content might be relevant to the keyword that was matched.

Your summary will be displayed in a web UI, so it must be professional and easy to read."""

# --- Core Application Logic ---
def get_llm_summary(client, prompt_text, model_deployment):
    if not client:
        return "[Error: OpenAI client not configured]"
    
    if is_paused_due_to_rate_limit.is_set():
        logging.warning("LLM request blocked because the system is paused due to a prior rate limit error.")
        return "[Status: Paused due to rate limit. Request not sent.]"

    try:
        response = client.chat.completions.create(
            model=model_deployment,
            messages=[
                {"role": "system", "content": CONTENT_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt_text}
            ],
        )
        summary = response.choices[0].message.content
        return summary.strip()

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

def process_and_queue_github_issue(issue, matched_label):
    logging.info(f"✅ GITHUB MATCH: Issue #{issue.get('number')} matched '{matched_label}'. Processing...")
    
    created_at_str = issue.get('created_at', '').replace('Z', '+00:00')
    unix_timestamp = 0
    if created_at_str:
        try:
            unix_timestamp = int(datetime.fromisoformat(created_at_str).timestamp())
        except ValueError:
            logging.warning(f"Could not parse timestamp: {created_at_str}")

    # Determine if the item is an issue or a pull request
    is_pr = 'pull_request' in issue and issue.get('pull_request') is not None
    item_subtype = "pull_request" if is_pr else "issue"

    item_data = {
        "id": f"gh-{issue.get('id')}",
        "type": "github_issue",
        "item_subtype": item_subtype,
        "by": issue.get('user', {}).get('login'),
        "time": unix_timestamp,
        "title": issue.get("title"),
        "url": issue.get("html_url"),
        "text": issue.get("body"),
        "matched_label": matched_label,
        "processed_at": datetime.utcnow().isoformat()
    }

    content_to_summarize = f"Issue Title: {item_data['title']}\n\nIssue Body:\n{item_data['text']}"
    prompt_text = (
        f"## Matched Keyword\n`{matched_label}`\n\n"
        f"## GitHub Issue Content to Analyze\n\n{content_to_summarize}"
    )
    
    ai_summary = get_llm_summary(client, prompt_text, DEPLOYMENT)
    item_data["ai_summary"] = ai_summary
    
    update_queue.put(item_data)

def check_if_issue_matches(issue):
    """Synchronously checks if an issue matches any pattern, returning the label if it does."""
    if not issue:
        return None
    content_to_check = f"{issue.get('title', '')} {issue.get('body', '')}"
    
    with patterns_lock:
        current_patterns = SEARCH_PATTERNS.copy()

    for label, pattern in current_patterns.items():
        if pattern.search(content_to_check):
            return label
    return None

def perform_github_scan(repo_string, start_page=1):
    if not repo_string or '/' not in repo_string:
        update_queue.put({"type": "status", "status": "error", "reason": f"Invalid repo format: {repo_string}"})
        return

    logging.info(f"GITHUB SCAN: Starting for repo '{repo_string}' from page {start_page}...")
    update_queue.put({"type": "status", "status": "scanning", "reason": f"Scanning repo: {repo_string}"})
    
    api_url = f"https://api.github.com/repos/{repo_string}/issues?state=all&per_page=100&page={start_page}"
    current_page = start_page
    matches_found_this_run = 0

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='GitHubScan') as executor:
            while api_url:
                if is_paused_due_to_rate_limit.is_set():
                    logging.warning("GitHub scan paused due to rate limit.")
                    break
                
                logging.info(f"Fetching page {current_page} of issues from {repo_string}...")
                response = requests.get(api_url, headers=HEADERS, timeout=20)
                response.raise_for_status()
                
                issues = response.json()
                if not issues:
                    logging.info("No more issues found.")
                    break
                
                for issue in issues:
                    matched_label = check_if_issue_matches(issue)
                    if matched_label:
                        matches_found_this_run += 1
                        executor.submit(process_and_queue_github_issue, issue, matched_label)
                    
                    if matches_found_this_run >= MAX_RESULTS_PER_RUN:
                        break # Break from issue loop
                
                if matches_found_this_run >= MAX_RESULTS_PER_RUN:
                    logging.info(f"Scan paused after reaching result limit of {MAX_RESULTS_PER_RUN}.")
                    update_queue.put({
                        "type": "status",
                        "status": "scan_paused",
                        "reason": f"Scan paused after finding {matches_found_this_run} items.",
                        "next_page": current_page + 1
                    })
                    return # End the function

                # Get next page URL from 'Link' header
                if 'link' in response.headers:
                    links = response.headers['link'].split(', ')
                    api_url = None
                    for link in links:
                        if 'rel="next"' in link:
                            api_url = link[link.find('<')+1:link.find('>')]
                            break
                else:
                    api_url = None
                current_page += 1

    except requests.RequestException as e:
        logging.error(f"GITHUB SCAN: Failed to fetch issues from {repo_string}: {e}")
        update_queue.put({"type": "status", "status": "error", "reason": f"Failed to fetch issues: {e}"})
        return
    except Exception as e:
        logging.error(f"GITHUB SCAN: An unexpected error occurred: {e}")
        update_queue.put({"type": "status", "status": "error", "reason": f"An unexpected error occurred: {e}"})
        return
    
    logging.info(f"GITHUB SCAN: Finished processing for repo '{repo_string}'.")
    update_queue.put({"type": "status", "status": "idle", "reason": f"Scan of {repo_string} complete."})


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

@app.route('/scan-github', methods=['POST'])
def scan_github():
    data = request.get_json()
    repo_string = data.get('repo')
    start_page = data.get('start_page', 1)
    if not repo_string:
        return jsonify({"error": "Missing 'repo' in request body"}), 400
    try:
        threading.Thread(target=perform_github_scan, args=(repo_string, start_page), daemon=True).start()
        return jsonify({"status": f"GitHub issue scan for repo '{repo_string}' initiated."})
    except Exception as e:
        logging.error(f"Error initiating GitHub scan: {e}")
        return jsonify({"error": str(e)}), 500

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
        matched_label = item.get('matched_label', 'Unknown')
        title = item.get('title') or f"Issue by {item.get('by', 'N/A')}"
        item_url = item.get('url')
        author = item.get('by', 'N/A')
        post_time = datetime.fromtimestamp(item.get('time', 0)).strftime('%B %d, %Y at %I:%M %p UTC')
        ai_summary = item.get('ai_summary', 'No summary available.')
        
        if isinstance(ai_summary, dict):
            ai_summary = ai_summary.get('answer', 'No summary available.')

        formatted_summary = ai_summary.replace('\n', '\n> ')

        slack_payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": { "type": "plain_text", "text": f"New '{matched_label}' Mention on GitHub" }
                },
                { "type": "section", "text": { "type": "mrkdwn", "text": f"*<{item_url}|{title}>*" } },
                { "type": "section", "text": { "type": "mrkdwn", "text": f"*AI Summary:*\n> {formatted_summary}" } },
                { "type": "context", "elements": [
                        {"type": "mrkdwn", "text": f"*Author:* `{author}`"},
                        {"type": "mrkdwn", "text": f"*Posted:* {post_time}"},
                        {"type": "mrkdwn", "text": f"*Source:* <{item_url}|View on GitHub>"}
                ]},
                {"type": "divider"},
                { "type": "context", "elements": [ {"type": "mrkdwn", "text": "Sent from The-Eye-Of-Sauron 👁️"} ] }
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

# --- HTML Template ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The-Eye-Of-Sauron 👁️ | GitHub Scanner</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #121921; color: #F9FAFB; }
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #212934; }
        ::-webkit-scrollbar-thumb { background: #4A5568; border-radius: 4px; }
        .brand-green { color: #00ED64; }
        .brand-dark-bg { background-color: #212934; }
        .brand-border { border-color: #4A5568; }
        .markdown-content pre { background-color: #0e131a; padding: 1rem; border-radius: 8px; overflow-x: auto; }
        .markdown-content code { background-color: #121921; color: #F9FAFB; padding: 0.2rem 0.4rem; border-radius: 4px; }
        .markdown-content p { margin-bottom: 0.5rem; }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .animate-fadeInUp { animation: fadeInUp 0.5s ease-out forwards; }
        .hidden { display: none; }
        .modal-overlay { transition: opacity 0.2s ease-in-out; }
        .sidebar-section { border-top: 1px solid #4A5568; padding-top: 1rem; margin-top: 1rem; }
    </style>
</head>
<body class="flex flex-col h-screen">

    <header class="flex items-center justify-between p-4 border-b brand-border shadow-lg flex-wrap gap-4">
        <div class="flex items-center space-x-3">
            <h1 class="text-2xl font-bold text-white">The-Eye-Of-Sauron <span class="brand-green">👁️</span></h1>
        </div>
        <div class="flex items-center space-x-4 flex-wrap gap-4">
             <div class="flex items-center space-x-2">
                <input type="password" id="slack-webhook-url" placeholder="Slack Webhook URL" class="w-48 p-2 text-sm rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-1 focus:ring-green-500 transition-all" title="Enter your Slack Webhook URL to enable sending notifications.">
                <button id="save-webhook-btn" class="px-3 py-2 text-sm font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-colors">Save</button>
            </div>
            <div id="status-indicator" class="flex items-center space-x-2">
                <span id="status-text" class="text-sm text-gray-400">Idle</span>
                <div id="status-dot" class="w-3 h-3 bg-gray-500 rounded-full"></div>
            </div>
        </div>
    </header>

    <main class="flex-1 flex flex-col md:flex-row overflow-hidden">
        <aside class="w-full md:w-1/3 lg:w-1/4 p-4 border-r brand-border overflow-y-auto flex flex-col">
            <div>
                <div class="flex justify-between items-center">
                    <h2 class="text-lg font-semibold">Manage Listeners</h2>
                    <button id="show-add-listener-modal-btn" class="px-3 py-1 text-sm font-semibold rounded-md bg-green-800 hover:bg-green-700 transition-colors"><i class="fa-solid fa-plus mr-2"></i>New</button>
                </div>
                <div id="listeners-list" class="mt-2 flex-1 space-y-2 overflow-y-auto pr-2"></div>
            </div>
            <div class="sidebar-section">
                <div class="flex justify-between items-center">
                    <h2 class="text-lg font-semibold">Manage Repositories</h2>
                    <button id="show-add-repo-modal-btn" class="px-3 py-1 text-sm font-semibold rounded-md bg-blue-800 hover:bg-blue-700 transition-colors"><i class="fa-solid fa-plus mr-2"></i>New</button>
                </div>
                <div id="repos-list" class="mt-2 flex-1 space-y-2 overflow-y-auto pr-2"></div>
            </div>
        </aside>
        <div id="feed-container" class="flex-1 h-full overflow-y-auto p-4 md:p-6 lg:p-8 space-y-6">
            <div id="controls-container" class="sticky top-4 z-10 bg-[#121921]/80 backdrop-blur-sm p-6 rounded-lg border brand-border flex flex-col gap-4 text-center">
                <div class="w-full flex flex-col md:flex-row items-center justify-center gap-4">
                    <div class="flex-1 w-full max-w-sm">
                        <select id="repo-selector" class="w-full p-2.5 text-base rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500 transition-all" title="Select a repository to scan."></select>
                    </div>
                    <div>
                        <button id="scan-github-btn" class="w-full px-6 py-2.5 font-semibold rounded-md bg-blue-700 hover:bg-blue-600 transition-colors text-base"><i class="fa-brands fa-github mr-2"></i>Scan Repository</button>
                    </div>
                </div>
                <div id="resume-controls" class="hidden w-full">
                    <p id="resume-reason" class="text-sm text-yellow-400 mb-2"></p>
                    <button id="resume-scan-btn" class="w-auto px-5 py-2 font-semibold rounded-md bg-green-700 hover:bg-green-600 transition-colors text-base"><i class="fa-solid fa-play mr-2"></i>Resume Scan</button>
                </div>
            </div>
            <div id="placeholder" class="text-center text-gray-500 pt-16">
                <i class="fas fa-search fa-3x"></i>
                <p class="mt-4 text-lg">No scan initiated.</p>
                <p class="text-sm">Select a repository and start a scan to see results.</p>
            </div>
        </div>
    </main>

    <div id="listener-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60 modal-overlay opacity-0">
        <div class="brand-dark-bg border brand-border rounded-lg shadow-2xl p-6 w-full max-w-md mx-4">
            <form id="listener-form">
                <h3 id="listener-modal-title" class="text-xl font-bold mb-4">Add New Listener</h3>
                <input type="hidden" id="original-listener-label">
                <div class="space-y-4">
                    <div>
                        <label for="listener-label" class="block text-sm font-medium">Label</label>
                        <input type="text" id="listener-label" placeholder="e.g., Bug Reports" required class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500">
                    </div>
                    <div>
                        <label for="listener-pattern" class="block text-sm font-medium">Regex Pattern</label>
                        <input type="text" id="listener-pattern" placeholder="e.g., (?i)bug" required class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500">
                    </div>
                </div>
                <div class="flex justify-end space-x-3 mt-6">
                    <button type="button" id="cancel-listener-btn" class="px-4 py-2 font-semibold rounded-md bg-gray-700 hover:bg-gray-600">Cancel</button>
                    <button type="submit" class="px-4 py-2 font-semibold rounded-md bg-green-800 hover:bg-green-700">Save Listener</button>
                </div>
            </form>
        </div>
    </div>
    <div id="repo-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60 modal-overlay opacity-0">
        <div class="brand-dark-bg border brand-border rounded-lg shadow-2xl p-6 w-full max-w-md mx-4">
            <form id="repo-form">
                <h3 id="repo-modal-title" class="text-xl font-bold mb-4">Add New Repository</h3>
                <input type="hidden" id="original-repo-name">
                <div>
                    <label for="repo-name" class="block text-sm font-medium">Repository</label>
                    <input type="text" id="repo-name" placeholder="owner/repository" required pattern="^[^/]+/[^/]+$" title="Must be in 'owner/repo' format." class="mt-1 w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>
                <div class="flex justify-end space-x-3 mt-6">
                    <button type="button" id="cancel-repo-btn" class="px-4 py-2 font-semibold rounded-md bg-gray-700 hover:bg-gray-600">Cancel</button>
                    <button type="submit" class="px-4 py-2 font-semibold rounded-md bg-blue-800 hover:bg-blue-700">Save Repository</button>
                </div>
            </form>
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
        scanGithubBtn: document.getElementById('scan-github-btn'),
        slackWebhookUrlInput: document.getElementById('slack-webhook-url'),
        saveWebhookBtn: document.getElementById('save-webhook-btn'),
        repoSelector: document.getElementById('repo-selector'),
        resumeControls: document.getElementById('resume-controls'),
        resumeReason: document.getElementById('resume-reason'),
        resumeScanBtn: document.getElementById('resume-scan-btn'),
        
        // Listener UI
        listenersList: document.getElementById('listeners-list'),
        listenerModal: document.getElementById('listener-modal'),
        listenerForm: document.getElementById('listener-form'),
        listenerModalTitle: document.getElementById('listener-modal-title'),
        listenerLabelInput: document.getElementById('listener-label'),
        listenerPatternInput: document.getElementById('listener-pattern'),
        originalListenerLabelInput: document.getElementById('original-listener-label'),
        showAddListenerModalBtn: document.getElementById('show-add-listener-modal-btn'),
        cancelListenerBtn: document.getElementById('cancel-listener-btn'),

        // Repo UI
        reposList: document.getElementById('repos-list'),
        repoModal: document.getElementById('repo-modal'),
        repoForm: document.getElementById('repo-form'),
        repoModalTitle: document.getElementById('repo-modal-title'),
        repoNameInput: document.getElementById('repo-name'),
        originalRepoNameInput: document.getElementById('original-repo-name'),
        showAddRepoModalBtn: document.getElementById('show-add-repo-modal-btn'),
        cancelRepoBtn: document.getElementById('cancel-repo-btn'),
    };

    let currentPatterns = [];
    let currentRepos = [];
    let eventSource = null;

    function loadJSONFromStorage(key, defaultValue) {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : defaultValue;
        } catch (e) {
            return defaultValue;
        }
    }
    
    function saveJSONToStorage(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    }

    // --- Config & Management UI ---
    function setupConfigControls() {
        ui.slackWebhookUrlInput.value = localStorage.getItem('slackWebhookUrl') || '';
        ui.saveWebhookBtn.addEventListener('click', () => {
            localStorage.setItem('slackWebhookUrl', ui.slackWebhookUrlInput.value.trim());
            ui.saveWebhookBtn.textContent = 'Saved!';
            setTimeout(() => { ui.saveWebhookBtn.textContent = 'Save'; }, 2000);
            updateAllSlackButtons();
        });
    }

    async function fetchPatterns() {
        try {
            const response = await fetch('/patterns');
            currentPatterns = await response.json();
            renderPatterns();
        } catch (e) { console.error("Failed to fetch patterns:", e); }
    }

    async function updatePatternsOnServer() {
        await fetch('/patterns', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(currentPatterns)
        });
    }

    function renderPatterns() {
        ui.listenersList.innerHTML = '';
        if (currentPatterns.length === 0) {
            ui.listenersList.innerHTML = '<p class="text-sm text-gray-500 italic p-2 text-center">Add a listener to begin.</p>';
            return;
        }
        currentPatterns.forEach(p => {
            const div = document.createElement('div');
            div.className = 'flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80';
            div.innerHTML = `
                <div class="flex-1 overflow-hidden">
                    <p class="font-semibold text-sm truncate" title="${p.label}">${p.label}</p>
                    <p class="text-xs text-gray-400 font-mono truncate" title="${p.pattern}">${p.pattern}</p>
                </div>
                <div class="flex items-center space-x-3 ml-2">
                    <button title="Edit Listener" class="edit-btn" data-type="listener" data-label="${p.label}" data-pattern="${p.pattern}"><i class="fa-solid fa-pencil text-gray-500 hover:text-blue-400"></i></button>
                    <button title="Remove Listener" class="remove-btn" data-type="listener" data-label="${p.label}"><i class="fa-solid fa-trash-can text-gray-500 hover:text-red-400"></i></button>
                </div>`;
            ui.listenersList.appendChild(div);
        });
    }

    function loadRepos() {
        currentRepos = loadJSONFromStorage('githubRepos', ['mastra-ai/mastra']);
        renderRepos();
        populateRepoSelector();
    }
    
    function renderRepos() {
        ui.reposList.innerHTML = '';
        if (currentRepos.length === 0) {
            ui.reposList.innerHTML = '<p class="text-sm text-gray-500 italic p-2 text-center">Add a repository to scan.</p>';
            return;
        }
        currentRepos.forEach(repo => {
            const div = document.createElement('div');
            div.className = 'flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80';
            div.innerHTML = `
                <p class="font-semibold text-sm truncate font-mono" title="${repo}">${repo}</p>
                <div class="flex items-center space-x-3 ml-2">
                    <button title="Edit Repository" class="edit-btn" data-type="repo" data-name="${repo}"><i class="fa-solid fa-pencil text-gray-500 hover:text-blue-400"></i></button>
                    <button title="Remove Repository" class="remove-btn" data-type="repo" data-name="${repo}"><i class="fa-solid fa-trash-can text-gray-500 hover:text-red-400"></i></button>
                </div>`;
            ui.reposList.appendChild(div);
        });
    }

    function populateRepoSelector() {
        ui.repoSelector.innerHTML = '';
        if (currentRepos.length === 0) {
            ui.repoSelector.innerHTML = '<option disabled>Please add a repository first</option>';
            ui.scanGithubBtn.disabled = true;
            return;
        }
        ui.scanGithubBtn.disabled = false;
        currentRepos.forEach(repo => {
            const option = document.createElement('option');
            option.value = repo;
            option.textContent = repo;
            ui.repoSelector.appendChild(option);
        });
    }

    function setupManagementEventListeners() {
        ui.showAddListenerModalBtn.addEventListener('click', () => openModal('listener'));
        ui.showAddRepoModalBtn.addEventListener('click', () => openModal('repo'));
        ui.cancelListenerBtn.addEventListener('click', () => closeModal('listener'));
        ui.cancelRepoBtn.addEventListener('click', () => closeModal('repo'));
        ui.listenerModal.addEventListener('click', (e) => { if(e.target === ui.listenerModal) closeModal('listener'); });
        ui.repoModal.addEventListener('click', (e) => { if(e.target === ui.repoModal) closeModal('repo'); });
        ui.listenerForm.addEventListener('submit', handleSave);
        ui.repoForm.addEventListener('submit', handleSave);
        
        document.body.addEventListener('click', e => {
            const btn = e.target.closest('.edit-btn, .remove-btn');
            if (btn) {
                const { type, ...data } = btn.dataset;
                if (btn.classList.contains('edit-btn')) openModal(type, data);
                if (btn.classList.contains('remove-btn')) handleRemove(type, data);
            }
        });
    }

    function openModal(type, data = null) {
        const isEdit = data !== null;
        const modal = ui[`${type}Modal`];
        const form = ui[`${type}Form`];
        const title = ui[`${type}ModalTitle`];

        form.reset();
        title.textContent = isEdit ? `Edit ${type.charAt(0).toUpperCase() + type.slice(1)}` : `Add New ${type.charAt(0).toUpperCase() + type.slice(1)}`;
        
        if (type === 'listener') {
            ui.originalListenerLabelInput.value = '';
            if (isEdit) {
                ui.listenerLabelInput.value = data.label;
                ui.listenerPatternInput.value = data.pattern;
                ui.originalListenerLabelInput.value = data.label;
            }
        } else if (type === 'repo') {
            ui.originalRepoNameInput.value = '';
            if (isEdit) {
                ui.repoNameInput.value = data.name;
                ui.originalRepoNameInput.value = data.name;
            }
        }
        modal.classList.remove('hidden');
        setTimeout(() => modal.classList.remove('opacity-0'), 10);
    }
    
    function closeModal(type) {
        const modal = ui[`${type}Modal`];
        modal.classList.add('opacity-0');
        setTimeout(() => modal.classList.add('hidden'), 200);
    }

    function handleSave(e) {
        e.preventDefault();
        const type = e.target.id.split('-')[0];
        
        if (type === 'listener') {
            const newLabel = ui.listenerLabelInput.value.trim();
            const originalLabel = ui.originalListenerLabelInput.value;
            if (!newLabel || !ui.listenerPatternInput.value.trim()) return;
            const isEditing = !!originalLabel;
            if (currentPatterns.some(p => p.label === newLabel) && (!isEditing || newLabel !== originalLabel)) {
                return alert("A listener with that label already exists.");
            }
            if (isEditing) {
                const p = currentPatterns.find(p => p.label === originalLabel);
                if (p) { p.label = newLabel; p.pattern = ui.listenerPatternInput.value.trim(); }
            } else {
                currentPatterns.push({ label: newLabel, pattern: ui.listenerPatternInput.value.trim() });
            }
            renderPatterns();
            updatePatternsOnServer();
        } else if (type === 'repo') {
            const newName = ui.repoNameInput.value.trim();
            const originalName = ui.originalRepoNameInput.value;
            if (!newName) return;
            const isEditing = !!originalName;
            if (currentRepos.includes(newName) && (!isEditing || newName !== originalName)) {
                return alert("That repository is already in the list.");
            }
            if (isEditing) {
                const index = currentRepos.indexOf(originalName);
                if (index > -1) currentRepos[index] = newName;
            } else {
                currentRepos.push(newName);
            }
            saveJSONToStorage('githubRepos', currentRepos);
            renderRepos();
            populateRepoSelector();
        }
        closeModal(type);
    }

    function handleRemove(type, data) {
        if (type === 'listener') {
            currentPatterns = currentPatterns.filter(p => p.label !== data.label);
            renderPatterns();
            updatePatternsOnServer();
        } else if (type === 'repo') {
            currentRepos = currentRepos.filter(repo => repo !== data.name);
            saveJSONToStorage('githubRepos', currentRepos);
            renderRepos();
            populateRepoSelector();
        }
    }

    // --- Core UI & Feed Logic ---
    function createFeedCard(item) {
        const card = document.createElement('div');
        card.className = 'feed-card brand-dark-bg border brand-border rounded-lg p-5 shadow-lg animate-fadeInUp';
        card.dataset.itemId = item.id;
        card.dataset.itemData = JSON.stringify(item);

        const postTime = new Date(item.time * 1000).toLocaleString('en-US', { timeZone: 'UTC' });
        const webhookUrl = localStorage.getItem('slackWebhookUrl') || '';
        
        // Create a visual indicator for issue vs. pull request
        let typeIndicatorHtml = '';
        if (item.item_subtype === 'pull_request') {
            typeIndicatorHtml = `<span class="inline-flex items-center text-xs font-medium text-purple-300 bg-purple-900/50 border border-purple-700 px-2 py-0.5 rounded-full"><i class="fa-solid fa-code-pull-request mr-1.5"></i> Pull Request</span>`;
        } else { // 'issue' or default
            typeIndicatorHtml = `<span class="inline-flex items-center text-xs font-medium text-green-300 bg-green-900/50 border border-green-700 px-2 py-0.5 rounded-full"><i class="fa-solid fa-exclamation-circle mr-1.5"></i> Issue</span>`;
        }

        card.innerHTML = `
            <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-2">
                <a href="${item.url}" target="_blank" class="text-xl font-bold text-white hover:text-green-400 mb-2 sm:mb-0 break-all">${item.title}</a>
                <span class="text-xs font-mono text-white bg-blue-900/70 border border-blue-700 px-2 py-1 rounded-full w-max shrink-0"><i class="fa-solid fa-tag mr-1"></i> ${item.matched_label}</span>
            </div>
            <div class="flex items-center flex-wrap gap-x-4 gap-y-2 text-xs text-gray-400 mt-2 border-b border-gray-700 pb-3 mb-3">
                ${typeIndicatorHtml}
                <span><i class="fa-solid fa-user mr-1"></i> ${item.by}</span>
                <span><i class="fa-solid fa-clock mr-1"></i> ${postTime} UTC</span>
                <a href="${item.url}" target="_blank" class="hover:text-green-400"><i class="fa-brands fa-github mr-1"></i> View on GitHub</a>
                <button class="send-to-slack-btn text-gray-400 hover:text-white text-xs" ${!webhookUrl ? 'disabled' : ''} title="${webhookUrl ? 'Send' : 'Enter a Slack Webhook URL'}">
                    <i class="fa-brands fa-slack mr-1"></i> Send to Slack
                </button>
            </div>
            <div>
                <h3 class="text-sm font-semibold text-green-400 mb-2">AI Summary</h3>
                <div class="markdown-content text-gray-300 text-sm">${marked.parse(item.ai_summary)}</div>
            </div>`;
        card.querySelector('.send-to-slack-btn').addEventListener('click', handleSendToSlack);
        return card;
    }

    function updateAllSlackButtons() {
        const url = localStorage.getItem('slackWebhookUrl') || '';
        document.querySelectorAll('.feed-card .send-to-slack-btn').forEach(btn => {
            btn.disabled = !url;
            btn.title = url ? 'Send to Slack' : 'Enter a Slack Webhook URL to enable';
        });
    }

    // --- Event Stream & Actions ---
    function setStatus(status, text) {
        ui.statusText.textContent = text;
        ui.statusDot.className = 'w-3 h-3 rounded-full'; // reset
        const statusClasses = {
            scanning: 'bg-yellow-500 animate-pulse',
            scan_paused: 'bg-yellow-500',
            rate_limit_paused: 'bg-red-500 animate-pulse',
            error: 'bg-red-500',
            idle: 'bg-gray-500'
        };
        ui.statusDot.classList.add(...(statusClasses[status] || statusClasses.idle).split(' '));
    }

    function handleStatusUpdate(data) {
        setStatus(data.status, data.reason || data.status);
        
        ui.scanGithubBtn.classList.remove('hidden');
        ui.resumeControls.classList.add('hidden');
        ui.scanGithubBtn.disabled = false;
        ui.scanGithubBtn.innerHTML = '<i class="fa-brands fa-github mr-2"></i>Scan Repository';

        if (data.status === 'scanning') {
            ui.scanGithubBtn.disabled = true;
            ui.scanGithubBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Scanning...';
        } else if (data.status === 'scan_paused') {
            ui.resumeReason.textContent = data.reason;
            ui.resumeScanBtn.dataset.nextPage = data.next_page;
            ui.resumeControls.classList.remove('hidden');
            ui.scanGithubBtn.innerHTML = '<i class="fa-solid fa-magnifying-glass mr-2"></i>Start New Scan';
        } else if (data.status === 'rate_limit_paused') {
            ui.controlsContainer.innerHTML = `
                <div class="text-center">
                    <p class="text-red-400 font-semibold">${data.reason || 'Paused due to API rate limit.'}</p>
                    <button id="resume-btn" class="mt-2 px-4 py-2 font-semibold rounded-md bg-green-700 hover:bg-green-600">
                        <i class="fa-solid fa-play mr-2"></i> Resume Operations
                    </button>
                </div>`;
            document.getElementById('resume-btn').addEventListener('click', handleResumeOperations);
        }
    }
    
    async function handleResumeOperations() {
        document.getElementById('resume-btn').innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Resuming...';
        await fetch('/resume-operations', { method: 'POST' });
        location.reload();
    }

    async function handleSendToSlack(e) {
        const btn = e.currentTarget;
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
            if (!response.ok) throw new Error((await response.json()).error);
            btn.innerHTML = '<i class="fa-solid fa-check mr-1"></i> Sent!';
        } catch (error) {
            btn.innerHTML = '<i class="fa-solid fa-xmark mr-1"></i> Failed';
            setTimeout(() => {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-brands fa-slack mr-1"></i> Send';
            }, 3000);
        }
    }

    function connectToStream() {
        if (eventSource) return;
        eventSource = new EventSource('/stream');
        
        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'status') {
                handleStatusUpdate(data);
            } else {
                ui.placeholder.classList.add('hidden');
                if (document.querySelector(`[data-item-id="${data.id}"]`)) return;
                const card = createFeedCard(data);
                ui.feedContainer.insertBefore(card, ui.feedContainer.querySelector('.feed-card'));
            }
        };

        eventSource.onerror = () => {
            setStatus('error', 'Connection Lost');
            eventSource.close(); eventSource = null;
            setTimeout(connectToStream, 5000);
        };
    }

    async function startScan(startPage = 1) {
        const repo = ui.repoSelector.value;
        if (!repo) {
            alert('Please select or add a repository to scan.');
            return;
        }

        if (startPage === 1) {
            // Clear feed for a new scan
            document.querySelectorAll('.feed-card').forEach(card => card.remove());
            ui.placeholder.classList.add('hidden');
        }

        ui.scanGithubBtn.disabled = true;
        ui.scanGithubBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Starting...';
        ui.resumeControls.classList.add('hidden');
        
        await fetch('/scan-github', { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo, start_page: startPage })
        });
    }

    // --- Initial Load ---
    async function initialize() {
        setupConfigControls();
        setupManagementEventListeners();
        loadRepos();
        await fetchPatterns();
        connectToStream();
        
        ui.scanGithubBtn.addEventListener('click', () => startScan(1));
        ui.resumeScanBtn.addEventListener('click', (e) => startScan(parseInt(e.currentTarget.dataset.nextPage, 10)));
    }
    
    initialize();
});
</script>
</body>
</html>
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

    print("--- The-Eye-Of-Sauron 👁️  (GitHub Scanner) ---")
    print(f"🚀 Starting server at http://{host}:{port}")
    print("👉 Open the URL in your browser to get started!")
    print("--------------------------------------------------")
    app.run(host=host, port=port, debug=False, threaded=True)
