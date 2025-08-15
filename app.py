#!/usr/bin/env python3
import os
import sys
import logging
import json
import re
import time
import queue
import threading
import concurrent.futures
from datetime import datetime, timedelta
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
monitor_thread = None
monitor_thread_lock = threading.Lock()
is_paused_due_to_rate_limit = threading.Event() # Global flag to pause operations

# --- Application Configuration ---
# --- API Endpoint Configuration (Now Generic) ---
# This section makes the script adaptable to other similar APIs.
# For Hacker News:
# - The 'max item' URL returns the ID of the latest item.
# - The 'item detail' URL is a template for fetching a specific item by its ID.
API_MAX_ITEM_URL = os.getenv("API_MAX_ITEM_URL", "https://hacker-news.firebaseio.com/v0/maxitem.json")
API_ITEM_DETAIL_URL_TEMPLATE = os.getenv("API_ITEM_DETAIL_URL_TEMPLATE", "https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
# Estimate of items per day for historical scans. Adjust if using a different API.
ITEMS_PER_DAY_ESTIMATE = int(os.getenv("ITEMS_PER_DAY_ESTIMATE", 40000))


# --- Keyword Monitoring Configuration ---
DEFAULT_PATTERNS = json.dumps([
    {"pattern": "(?i)mongodb", "label": "MongoDB"},
    {"pattern": "(?i)vector search", "label": "Vector Search"},
    {"pattern": "(?i)openai", "label": "OpenAI"}
])
SEARCH_PATTERNS_JSON = os.getenv("SEARCH_PATTERNS", DEFAULT_PATTERNS)
SEARCH_PATTERNS = {}
patterns_lock = threading.Lock()

# --- Service Configuration ---
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 25))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
# Separate executors for different tasks
initial_fetch_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix='InitialFetch')
historical_scan_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='HistoricalScan')


# --- Azure OpenAI Client Configuration ---
try:
    client = AzureOpenAI(
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    )
    DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "o4-mini")
    if not all([os.getenv("AZURE_OPENAI_ENDPOINT"), os.getenv("AZURE_OPENAI_API_KEY"), DEPLOYMENT]):
        raise ValueError("One or more required Azure environment variables are missing.")
    logging.info(f"Azure OpenAI client configured successfully for deployment: {DEPLOYMENT}")
except Exception as e:
    logging.error(f"Error initializing Azure OpenAI client: {e}")
    client = None

# --- Prompt Templates ---
CONTENT_SUMMARY_SYSTEM_PROMPT = """You are an expert AI analyst. Your task is to analyze the provided HTML content from a link and provide a concise, insightful summary for a business and technical audience.

**Core Directives:**
- **Summarize Key Points:** Distill the main topic, key arguments, and overall sentiment.
- **Ignore Boilerplate:** Disregard HTML tags, navigation, ads, and irrelevant text.
- **Format for Clarity:** Your final output must be a single, well-written paragraph of no more than 150 words.
- **Be Objective:** Do not add personal opinions, disclaimers, or apologies.
- **Ground Your Answer:** Base your summary *only* on the provided text.
- **Identify Relevance:** Briefly mention why this content might be relevant to the keyword that was matched.

Your summary will be displayed in a web UI, so it must be professional and easy to read."""

# --- Core Application Logic ---
def get_reasoned_llm_response(client, prompt_text, model_deployment, effort="medium"):
    """
    Calls the LLM endpoint. If a rate limit error occurs, it sets a global
    pause flag and notifies the frontend. It no longer retries automatically.
    """
    if not client:
        return {"answer": "[Error: OpenAI client not configured]", "summaries": []}
    
    # Check if operations are paused before making a new request
    if is_paused_due_to_rate_limit.is_set():
        logging.warning("LLM request blocked because the system is paused due to a prior rate limit error.")
        return {"answer": "[Status: Paused due to rate limit. Request not sent.]", "summaries": []}

    try:
        response = client.responses.create(
            input=prompt_text,
            model=model_deployment,
            reasoning={"effort": effort, "summary": "detailed"}
        )
        
        response_data = response.model_dump()
        result = {"answer": "Could not extract a final answer.", "summaries": []}
        output_blocks = response_data.get("output", [])
        
        if output_blocks:
            summary_section = output_blocks[0].get("summary", [])
            if summary_section:
                result["summaries"] = [s.get("text") for s in summary_section if s.get("text")]
  
            content_section_index = 1 if summary_section else 0
  
            if len(output_blocks) > content_section_index and output_blocks[content_section_index].get("content"):
                result["answer"] = output_blocks[content_section_index]["content"][0].get("text", result["answer"])
  
            if result["answer"] == "Could not extract a final answer.":
                for block in output_blocks:
                    if block.get("content"):
                        for content_item in block["content"]:
                            if content_item.get("text"):
                                result["answer"] = content_item["text"]
                                break
                    if result["answer"] != "Could not extract a final answer.":
                        break
  
        result["answer"] = result["answer"].strip()
        return result

    except Exception as e:
        error_str = str(e).lower()
        # Check if it's a rate limit error
        if "rate limit" in error_str:
            logging.warning(f"RATE LIMIT EXCEEDED. Pausing all LLM requests. Error: {e}")
            is_paused_due_to_rate_limit.set() # Set the global pause flag
            
            # Send a status update to the frontend via the queue
            reason = "Rate limit exceeded. Please wait a moment before resuming."
            match = re.search(r'try again in ([\d\.]+) seconds', error_str)
            if match:
                reason = f"Rate limit exceeded. The API suggests waiting {match.group(1)} seconds. Please wait and then click 'Resume'."
            
            update_queue.put({"type": "status", "status": "paused", "reason": reason})
            return {"answer": f"[Error: Paused due to rate limit: {e}]", "summaries": []}
        else:
            # Handle other types of errors
            logging.error(f"An unexpected error occurred in get_reasoned_llm_response: {e}")
            return {"answer": f"[Error calling LLM: {e}]", "summaries": []}

def fetch_html(url):
    try:
        logging.info(f"Fetching content from {url}...")
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logging.error(f"Failed to fetch HTML from {url}: {e}")
        return f"Could not fetch content from URL: {e}"

def fetch_item(item_id):
    try:
        item_url = API_ITEM_DETAIL_URL_TEMPLATE.format(item_id=item_id)
        response = requests.get(item_url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Error fetching item {item_id}: {e}")
        return None

def process_and_queue_item(item, matched_label):
    """Shared logic to process a matched item and push it to the queue."""
    if not item or not isinstance(item, dict):
        logging.warning(f"Skipping processing of invalid item: {item}")
        return

    logging.info(f"✅ MATCH FOUND: Item {item.get('id', 'N/A')} matched pattern '{matched_label}'.")
    
    item_data = {
        "id": item.get('id'),
        "type": item.get("type"),
        "by": item.get("by"),
        "time": item.get("time"),
        "title": item.get("title"),
        "url": item.get("url"),
        "text": item.get("text"),
        "matched_label": matched_label,
        "processed_at": datetime.utcnow().isoformat()
    }

    # --- REVISED LOGIC TO AVOID 403 ERRORS ---
    content_to_summarize = ""
    source_type = ""

    # 1. Prioritize external URLs. These are standard story links.
    if item.get("url"):
        html_content = fetch_html(item.get("url"))
        if "Could not fetch content" not in html_content:
            content_to_summarize = html_content
            source_type = "HTML Content"
        else:
            # Set error summary if fetching the external URL fails
            ai_summary = {"answer": f"Could not fetch content from the URL to generate a summary. Error: {html_content}", "summaries": []}

    # 2. If no external URL, it's a comment or text post. Use its text from the API.
    #    This completely avoids scraping news.ycombinator.com.
    elif item.get("text"):
        content_to_summarize = item.get("text")
        source_type = "Text Content"

    # 3. Generate summary only if we have content
    if content_to_summarize and source_type:
        prompt_text = (
            f"{CONTENT_SUMMARY_SYSTEM_PROMPT}\n\n"
            f"## Matched Keyword\n`{matched_label}`\n\n"
            f"## {source_type} to Analyze\n\n{content_to_summarize}"
        )
        ai_summary = get_reasoned_llm_response(client, prompt_text, DEPLOYMENT)
    # 4. If there was a fetch error or no content, handle it
    elif 'ai_summary' not in locals():
        ai_summary = {"answer": "No content available to summarize.", "summaries": []}
    # --- END REVISED LOGIC ---

    item_data["ai_summary"] = ai_summary
    update_queue.put(item_data)
    return item_data # Return for initial fetch
    
def check_item_for_match(item_id):
    """Fetches a single item and checks it against all current patterns."""
    if is_paused_due_to_rate_limit.is_set():
        return None # Stop processing if paused

    item = fetch_item(item_id)
    if not item or item.get("deleted") or item.get("dead"):
        return None

    content_to_check = f"{item.get('title', '')} {item.get('text', '')}"
    
    with patterns_lock:
        current_patterns = SEARCH_PATTERNS.copy()

    for label, pattern in current_patterns.items():
        if pattern.search(content_to_check):
            # Use a separate thread to process and queue to not block the check
            threading.Thread(target=process_and_queue_item, args=(item, label)).start()
            return # Process only for the first match
    return None

def check_and_process_item_for_initial_fetch(item_id):
    """Variant for initial fetch that processes synchronously and returns data."""
    if is_paused_due_to_rate_limit.is_set():
        return None # Stop processing if paused

    item = fetch_item(item_id)
    if not item or item.get("deleted") or item.get("dead"):
        return None

    content_to_check = f"{item.get('title', '')} {item.get('text', '')}"

    with patterns_lock:
        current_patterns = SEARCH_PATTERNS.copy()

    for label, pattern in current_patterns.items():
        if pattern.search(content_to_check):
            # Process synchronously to return the result directly
            return process_and_queue_item(item, label)
    return None

def perform_historical_scan(days=7):
    """Scans backwards in time for all patterns."""
    logging.info(f"HISTORICAL SCAN: Starting for all listeners, going back {days} days...")
    try:
        max_item_id = requests.get(API_MAX_ITEM_URL, timeout=10).json()
    except requests.RequestException as e:
        logging.error(f"HISTORICAL SCAN: Failed to start: {e}")
        return

    items_to_scan = ITEMS_PER_DAY_ESTIMATE * days
    start_id = max_item_id
    end_id = max(0, start_id - items_to_scan)
    
    logging.info(f"Scanning from item {start_id} to {end_id}...")
    item_ids_to_check = range(start_id, end_id, -1)
    
    # Use the historical executor to scan items. It will stop processing new items
    # if the pause flag is set by one of the running threads.
    historical_scan_executor.map(check_item_for_match, item_ids_to_check)

    logging.info(f"HISTORICAL SCAN: Finished for all listeners.")


def monitor_api():
    """The main background thread for live monitoring."""
    logging.info("Starting API monitoring thread...")
    seen_ids = set()
    
    try:
        last_max_id = requests.get(API_MAX_ITEM_URL, timeout=10).json()
        logging.info(f"Starting scan from item ID: {last_max_id}")
    except requests.RequestException as e:
        logging.error(f"Could not fetch initial max item ID. Retrying... Error: {e}")
        time.sleep(60)
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='LiveMonitor') as executor:
        while True:
            try:
                # If paused, wait here until the flag is cleared by the user.
                if is_paused_due_to_rate_limit.is_set():
                    logging.info("Monitoring is paused due to rate limit. Waiting for user to resume...")
                    time.sleep(5) # Check every 5 seconds
                    continue # Skip to the next loop iteration to re-check the flag

                current_max_id = requests.get(API_MAX_ITEM_URL, timeout=10).json()
                if current_max_id > last_max_id:
                    logging.info(f"{current_max_id - last_max_id} new items detected. Processing...")
                    new_ids = [i for i in range(last_max_id + 1, current_max_id + 1) if i not in seen_ids]
                    
                    if new_ids:
                        executor.map(check_item_for_match, new_ids)
                        seen_ids.update(new_ids)
                    
                    last_max_id = current_max_id
                
                if len(seen_ids) > 20000:
                    oldest_ids = sorted(list(seen_ids))[:10000]
                    for i in oldest_ids: seen_ids.remove(i)
                    logging.info("Cleaned up in-memory cache of seen IDs.")

                time.sleep(30)
            except requests.RequestException as e:
                logging.error(f"Error polling for max item ID: {e}. Retrying in 60s.")
                time.sleep(60)
            except Exception as e:
                logging.error(f"An unexpected error occurred in the monitor loop: {e}")
                time.sleep(60)

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
    """Thread-safe way to update search patterns."""
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

@app.route('/initial-items')
def initial_items():
    """Fetches and processes the first 10 items on page load."""
    try:
        max_item_id = requests.get(API_MAX_ITEM_URL, timeout=10).json()
        item_ids_to_fetch = range(max_item_id, max_item_id - 10, -1)
        
        results = []
        # Use an executor to fetch and process in parallel
        future_to_id = {initial_fetch_executor.submit(check_and_process_item_for_initial_fetch, item_id): item_id for item_id in item_ids_to_fetch}
        for future in concurrent.futures.as_completed(future_to_id):
            result = future.result()
            if result:
                results.append(result)
        
        # Sort results by time, descending (most recent first)
        results.sort(key=lambda x: x.get('time', 0), reverse=True)
        return jsonify(results)

    except requests.RequestException as e:
        logging.error(f"Could not fetch initial items: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/start-monitoring', methods=['POST'])
def start_monitoring():
    """Starts the background monitoring thread if it's not already running."""
    global monitor_thread
    with monitor_thread_lock:
        if monitor_thread is None or not monitor_thread.is_alive():
            monitor_thread = threading.Thread(target=monitor_api, daemon=True)
            monitor_thread.start()
            logging.info("Live monitoring thread started by user request.")
            return jsonify({"status": "Monitoring started."})
        else:
            logging.info("Live monitoring was already running.")
            return jsonify({"status": "Monitoring was already active."})

@app.route('/scan-historical', methods=['POST'])
def scan_historical():
    """Kicks off a historical scan in a background thread."""
    try:
        days = int(request.args.get('days', 7))
        # Run the scan in a background thread so the request returns immediately
        threading.Thread(target=perform_historical_scan, args=(days,), daemon=True).start()
        return jsonify({"status": f"Historical scan for the last {days} days initiated."})
    except Exception as e:
        logging.error(f"Error initiating historical scan: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/resume-monitoring', methods=['POST'])
def resume_monitoring():
    """Clears the rate limit flag to resume operations."""
    if is_paused_due_to_rate_limit.is_set():
        is_paused_due_to_rate_limit.clear()
        logging.info("Rate limit flag cleared by user. Resuming operations.")
        # Let the frontend know we're resuming
        update_queue.put({"type": "status", "status": "resumed", "reason": "Monitoring resumed by user."})
        return jsonify({"status": "Resumed monitoring."})
    else:
        return jsonify({"status": "Monitoring was not paused."})

@app.route('/send-to-slack', methods=['POST'])
def send_to_slack():
    """Formats and sends a notification to a Slack webhook."""
    data = request.get_json()
    item = data.get('item')
    webhook_url = data.get('webhookUrl')

    if not item or not webhook_url:
        return jsonify({"error": "Missing item data or webhook URL"}), 400
    
    try:
        # Prepare data for Slack message
        matched_label = item.get('matched_label', 'Unknown')
        # Correctly handle title for comments vs. stories
        title = item.get('title') or f"Comment by {item.get('by', 'N/A')}"
        item_url = item.get('url') # Will be None for comments
        hn_url = f"https://news.ycombinator.com/item?id={item.get('id')}"
        author = item.get('by', 'N/A')
        post_time = datetime.fromtimestamp(item.get('time', 0)).strftime('%B %d, %Y at %I:%M %p')
        ai_summary = item.get('ai_summary', {}).get('answer', 'No summary available.')
        formatted_summary = ai_summary.replace('\n', '\n> ')

        # Conditionally create the title block to avoid broken links for comments
        if item_url:
            title_block = {
                "type": "section",
                "text": { "type": "mrkdwn", "text": f"*<{item_url}|{title}>*" }
            }
        else:
            title_block = {
                "type": "section",
                "text": { "type": "mrkdwn", "text": f"*{title}*" }
            }

        # Slack Block Kit Payload
        slack_payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"New Mention of '{matched_label}' Found"
                    }
                },
                title_block,
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
                        {"type": "mrkdwn", "text": f"*Source:* <{hn_url}|View on Hacker News>"}
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
        response.raise_for_status() # Raise an exception for bad status codes
        
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
    <title>The-Eye-Of-Sauron 👁️</title>
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
        .markdown-content pre { background-color: #0e131a; padding: 1rem; border-radius: 8px; }
        .markdown-content code { background-color: #121921; color: #F9FAFB; padding: 0.2rem 0.4rem; border-radius: 4px; }
        details > summary { cursor: pointer; list-style: none; }
        details > summary::-webkit-details-marker { display: none; }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .animate-fadeInUp { animation: fadeInUp 0.5s ease-out forwards; }
        .storage-bar-gradient { background: linear-gradient(to right, #22c55e, #facc15, #ef4444); }
        .hidden { display: none; }
        .modal-overlay {
            transition: opacity 0.2s ease-in-out;
        }
    </style>
</head>
<body class="flex flex-col h-screen">

    <header class="flex items-center justify-between p-4 border-b brand-border shadow-lg flex-wrap gap-4">
        <div class="flex items-center space-x-3">
            <h1 class="text-2xl font-bold text-white">The-Eye-Of-Sauron <span class="brand-green">👁️</span></h1>
        </div>
        <div class="flex items-center space-x-4">
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
        <aside class="w-full md:w-1/3 lg:w-1/4 p-4 border-r brand-border overflow-y-auto flex flex-col space-y-4">
            <div class="flex justify-between items-center">
                <h2 class="text-lg font-semibold flex items-center gap-2">Manage Listeners <i class="fa-solid fa-circle-info text-gray-500 text-sm" title="Listeners are saved search patterns that actively monitor the feed for new items."></i></h2>
                <button id="show-add-listener-modal-btn" class="px-3 py-1 text-sm font-semibold rounded-md bg-green-800 hover:bg-green-700 transition-colors"><i class="fa-solid fa-plus mr-2"></i>New</button>
            </div>
            <div id="listeners-list" class="flex-1 space-y-2 overflow-y-auto pr-2"></div>
        </aside>
        <div id="feed-container" class="flex-1 h-full overflow-y-auto p-4 md:p-6 lg:p-8 space-y-6">
             <div id="placeholder" class="text-center text-gray-500 pt-16">
                <i class="fas fa-spinner fa-spin fa-3x"></i>
                <p class="mt-4 text-lg">Fetching initial items...</p>
                <p class="text-sm">Please wait a moment.</p>
            </div>
            <div id="controls-container" class="hidden sticky top-4 z-10 bg-[#121921]/80 backdrop-blur-sm p-6 rounded-lg border brand-border flex flex-col md:flex-row items-center justify-center gap-6 text-center">
                <div>
                    <button id="start-live-btn" class="w-full px-5 py-2.5 font-semibold rounded-md bg-blue-700 hover:bg-blue-600 transition-colors text-base"><i class="fa-solid fa-satellite-dish mr-2"></i>Listen for Live Updates</button>
                    <p class="text-xs text-gray-400 mt-2">Monitor new items in real-time.</p>
                </div>
                <div class="text-gray-500">or</div>
                <div>
                    <button id="scan-historical-btn" class="w-full px-5 py-2.5 font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-colors text-base"><i class="fa-solid fa-clock-rotate-left mr-2"></i>Scan Past 7 Days</button>
                    <p class="text-xs text-gray-400 mt-2">Find mentions from the previous week.</p>
                </div>
            </div>
        </div>
    </main>

    <div id="listener-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/60 modal-overlay opacity-0">
        <div class="brand-dark-bg border brand-border rounded-lg shadow-2xl p-6 w-full max-w-md mx-4">
            <form id="listener-form">
                <h3 id="modal-title" class="text-xl font-bold mb-4">Add New Listener</h3>
                <input type="hidden" id="original-listener-label">
                <div class="space-y-4">
                    <div>
                        <label for="listener-label" class="block text-sm font-medium text-gray-300 mb-1">Label</label>
                        <input type="text" id="listener-label" placeholder="e.g., MongoDB Mentions" required class="w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500">
                        <p class="text-xs text-gray-400 mt-1">A short, descriptive name for your search.</p>
                    </div>
                    <div>
                        <label for="listener-pattern" class="block text-sm font-medium text-gray-300 mb-1">Regex Pattern</label>
                        <input type="text" id="listener-pattern" placeholder="e.g., (?i)mongo(db)?" required class="w-full p-2 rounded-md bg-gray-800 border brand-border focus:outline-none focus:ring-2 focus:ring-green-500">
                        <p class="text-xs text-gray-400 mt-1">Use regex for matching. `(?i)` makes it case-insensitive.</p>
                    </div>
                </div>
                <div class="flex justify-end space-x-3 mt-6">
                    <button type="button" id="cancel-listener-btn" class="px-4 py-2 font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-colors">Cancel</button>
                    <button type="submit" id="save-listener-btn" class="px-4 py-2 font-semibold rounded-md bg-green-800 hover:bg-green-700 transition-colors">Save Listener</button>
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
        listenersList: document.getElementById('listeners-list'),
        controlsContainer: document.getElementById('controls-container'),
        startLiveBtn: document.getElementById('start-live-btn'),
        scanHistoricalBtn: document.getElementById('scan-historical-btn'),
        slackWebhookUrlInput: document.getElementById('slack-webhook-url'),
        saveWebhookBtn: document.getElementById('save-webhook-btn'),
        // Modal UI
        listenerModal: document.getElementById('listener-modal'),
        listenerForm: document.getElementById('listener-form'),
        modalTitle: document.getElementById('modal-title'),
        listenerLabelInput: document.getElementById('listener-label'),
        listenerPatternInput: document.getElementById('listener-pattern'),
        originalListenerLabelInput: document.getElementById('original-listener-label'),
        showAddListenerModalBtn: document.getElementById('show-add-listener-modal-btn'),
        cancelListenerBtn: document.getElementById('cancel-listener-btn'),
        saveListenerBtn: document.getElementById('save-listener-btn'),
    };

    let currentPatterns = [];
    let eventSource = null;
    let isLive = false;

    // --- Slack Webhook Management ---
    function saveWebhookUrl() {
        const url = ui.slackWebhookUrlInput.value.trim();
        if (url) {
            localStorage.setItem('slackWebhookUrl', url);
            ui.saveWebhookBtn.textContent = 'Saved!';
            setTimeout(() => { ui.saveWebhookBtn.textContent = 'Save'; }, 2000);
        } else {
            localStorage.removeItem('slackWebhookUrl');
        }
        // Refresh cards to update slack button state
        document.querySelectorAll('.feed-card').forEach(card => {
            const slackBtn = card.querySelector('.send-to-slack-btn');
            if (slackBtn) {
                slackBtn.disabled = !url;
                slackBtn.title = url ? 'Send to Slack' : 'Enter a Slack Webhook URL to enable';
            }
        });
    }

    function loadWebhookUrl() {
        const url = localStorage.getItem('slackWebhookUrl');
        if (url) {
            ui.slackWebhookUrlInput.value = url;
        }
    }

    // --- Listener (Pattern) Management ---
    async function fetchPatterns() {
        try {
            const response = await fetch('/patterns');
            currentPatterns = await response.json();
            renderPatterns();
        } catch (e) { console.error("Failed to fetch patterns:", e); }
    }

    async function updatePatternsOnServer() {
        try {
            await fetch('/patterns', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(currentPatterns)
            });
        } catch (e) {
            console.error("Failed to update patterns on server:", e);
        }
    }

    function renderPatterns() {
        ui.listenersList.innerHTML = '';
        if (currentPatterns.length === 0) {
            ui.listenersList.innerHTML = '<p class="text-sm text-gray-500 italic p-2 text-center">Add a listener to begin monitoring.</p>';
            return;
        }
        currentPatterns.forEach(p => {
            const div = document.createElement('div');
            div.className = 'flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80 transition-colors';
            div.innerHTML = `
                <div class="flex-1 overflow-hidden">
                    <p class="font-semibold text-sm truncate" title="${p.label}">${p.label}</p>
                    <p class="text-xs text-gray-400 font-mono truncate" title="${p.pattern}">${p.pattern}</p>
                </div>
                <div class="flex items-center space-x-3 ml-2">
                    <button title="Edit Listener" class="edit-listener-btn text-gray-500 hover:text-blue-400 transition-colors" data-label="${p.label}" data-pattern="${p.pattern}"><i class="fa-solid fa-pencil"></i></button>
                    <button title="Remove Listener" class="remove-listener-btn text-gray-500 hover:text-red-400 transition-colors" data-label="${p.label}"><i class="fa-solid fa-trash-can"></i></button>
                </div>
            `;
            ui.listenersList.appendChild(div);
        });
        
        document.querySelectorAll('.edit-listener-btn').forEach(btn => btn.addEventListener('click', handleEditListener));
        document.querySelectorAll('.remove-listener-btn').forEach(btn => btn.addEventListener('click', handleRemoveListener));
    }

    function handleSaveListener(e) {
        e.preventDefault();
        const newLabel = ui.listenerLabelInput.value.trim();
        const newPattern = ui.listenerPatternInput.value.trim();
        const originalLabel = ui.originalListenerLabelInput.value;
        const isEditing = originalLabel !== "";

        if (!newLabel || !newPattern) {
            alert("Both label and pattern are required.");
            return;
        }

        // Check for duplicate label, but allow if it's the same item being edited
        if (currentPatterns.some(p => p.label === newLabel) && (!isEditing || newLabel !== originalLabel)) {
            alert("A listener with that label already exists.");
            return;
        }

        if (isEditing) {
            const patternToUpdate = currentPatterns.find(p => p.label === originalLabel);
            if (patternToUpdate) {
                patternToUpdate.label = newLabel;
                patternToUpdate.pattern = newPattern;
            }
        } else {
            currentPatterns.push({ label: newLabel, pattern: newPattern });
        }

        renderPatterns();
        updatePatternsOnServer();
        closeListenerModal();
    }
    
    function handleEditListener(e) {
        const btn = e.currentTarget;
        const label = btn.dataset.label;
        const pattern = btn.dataset.pattern;
        openListenerModal('edit', { label, pattern });
    }

    function handleRemoveListener(e) {
        const labelToRemove = e.currentTarget.dataset.label;
        currentPatterns = currentPatterns.filter(p => p.label !== labelToRemove);
        renderPatterns();
        updatePatternsOnServer();
    }

    // --- Modal Management ---
    function openListenerModal(mode = 'add', data = null) {
        ui.listenerForm.reset();
        ui.originalListenerLabelInput.value = '';
        
        if (mode === 'edit' && data) {
            ui.modalTitle.textContent = 'Edit Listener';
            ui.saveListenerBtn.textContent = 'Save Changes';
            ui.listenerLabelInput.value = data.label;
            ui.listenerPatternInput.value = data.pattern;
            ui.originalListenerLabelInput.value = data.label; // Track original for updates
        } else {
            ui.modalTitle.textContent = 'Add New Listener';
            ui.saveListenerBtn.textContent = 'Add Listener';
        }
        
        ui.listenerModal.classList.remove('hidden');
        setTimeout(() => ui.listenerModal.classList.remove('opacity-0'), 10); // For transition
    }

    function closeListenerModal() {
        ui.listenerModal.classList.add('opacity-0');
        setTimeout(() => ui.listenerModal.classList.add('hidden'), 200); // Wait for transition
    }


    // --- UI Rendering ---
    function createFeedCard(item) {
        const card = document.createElement('div');
        card.className = 'feed-card brand-dark-bg border brand-border rounded-lg p-5 shadow-lg animate-fadeInUp';
        card.dataset.itemId = item.id;
        card.dataset.itemData = JSON.stringify(item); // Store full item data

        const itemUrl = item.url || `https://news.ycombinator.com/item?id=${item.id}`;
        const hnUrl = `https://news.ycombinator.com/item?id=${item.id}`;
        const postTime = new Date(item.time * 1000).toLocaleString();
        const webhookUrl = localStorage.getItem('slackWebhookUrl');

        let titleHtml = item.title 
            ? `<a href="${itemUrl}" target="_blank" class="text-xl font-bold text-white hover:text-green-400">${item.title}</a>`
            : `<span class="text-xl font-bold text-white">Comment by ${item.by}</span>`;

        let reasoningHtml = '';
        if (item.ai_summary.summaries && item.ai_summary.summaries.length > 0) {
            const summaryItems = item.ai_summary.summaries.map(s => `<li>${marked.parseInline(s)}</li>`).join('');
            reasoningHtml = `<details class="bg-gray-900/50 border border-gray-700 rounded-md mt-4">
                <summary class="p-2 cursor-pointer text-xs font-semibold flex items-center text-gray-300"><i class="fa-solid fa-chevron-right w-4 mr-2 transition-transform"></i>Show AI Reasoning</summary>
                <div class="p-3 border-t border-gray-700 markdown-content text-xs"><ul class="list-disc pl-5">${summaryItems}</ul></div>
            </details>`;
        }

        card.innerHTML = `
            <div class="flex justify-between items-start">
                <div class="flex-1">
                    <div class="flex flex-col sm:flex-row justify-between sm:items-center">
                        ${titleHtml}
                        <span class="text-xs font-mono text-white bg-green-900/70 border border-green-700 px-2 py-1 rounded-full mt-2 sm:mt-0 w-max"><i class="fa-solid fa-tag mr-1"></i> ${item.matched_label}</span>
                    </div>
                    <div class="flex items-center flex-wrap gap-x-4 gap-y-2 text-xs text-gray-400 mt-2 border-b border-gray-700 pb-3 mb-3">
                        <span><i class="fa-solid fa-user mr-1"></i> ${item.by}</span>
                        <span><i class="fa-solid fa-clock mr-1"></i> ${postTime}</span>
                        <a href="${hnUrl}" target="_blank" class="hover:text-green-400"><i class="fa-brands fa-hacker-news mr-1"></i> View on HN</a>
                        <button class="send-to-slack-btn text-gray-400 hover:text-white transition-colors text-xs" ${!webhookUrl ? 'disabled' : ''} title="${webhookUrl ? 'Send to Slack' : 'Enter a Slack Webhook URL to enable'}">
                            <i class="fa-brands fa-slack mr-1"></i> Send to Slack
                        </button>
                    </div>
                </div>
            </div>
            <div class="mt-4">
                <h3 class="text-sm font-semibold text-green-400 mb-2">AI Summary</h3>
                <div class="markdown-content text-gray-300 text-sm">${marked.parse(item.ai_summary.answer)}</div>
            </div>
            ${reasoningHtml}
        `;
        
        card.querySelector('details')?.addEventListener('toggle', (e) => e.target.querySelector('i').classList.toggle('rotate-90'));
        card.querySelector('.send-to-slack-btn').addEventListener('click', handleSendToSlack);
        return card;
    }
    
    function renderInitialItems(items) {
        ui.placeholder.classList.add('hidden');
        if (items.length === 0) {
            const noMatches = document.createElement('div');
            noMatches.id = 'no-matches-placeholder';
            noMatches.className = 'text-center text-gray-500 pt-16';
            noMatches.innerHTML = `
                <i class="fas fa-search fa-3x"></i>
                <p class="mt-4 text-lg">No matches found in the 10 most recent items.</p>
                <p class="text-sm">Add a listener and start live monitoring or scan the past to find results.</p>
            `;
            ui.feedContainer.appendChild(noMatches);
        } else {
            items.forEach(item => ui.feedContainer.appendChild(createFeedCard(item)));
        }
        ui.controlsContainer.classList.remove('hidden');
        ui.feedContainer.prepend(ui.controlsContainer);
    }

    // --- Event Stream & Actions ---
    function handleStatusUpdate(data) {
        if (data.status === 'paused') {
            isLive = false;
            ui.statusText.textContent = 'Paused (Rate Limit)';
            ui.statusDot.classList.remove('bg-green-500', 'bg-yellow-500');
            ui.statusDot.classList.add('bg-red-500', 'animate-pulse');
            ui.controlsContainer.innerHTML = `
                <div class="text-center">
                    <p class="text-red-400 font-semibold">${data.reason || 'Paused due to API rate limit.'}</p>
                    <button id="resume-btn" class="mt-2 px-4 py-2 font-semibold rounded-md bg-green-700 hover:bg-green-600 transition-colors">
                        <i class="fa-solid fa-play mr-2"></i> Resume Monitoring
                    </button>
                </div>
            `;
            document.getElementById('resume-btn').addEventListener('click', handleResume);
        } else if (data.status === 'resumed') {
            isLive = true;
            ui.statusText.textContent = 'Live';
            ui.statusDot.classList.remove('bg-red-500', 'bg-yellow-500', 'animate-pulse');
            ui.statusDot.classList.add('bg-green-500');
            ui.controlsContainer.innerHTML = '<p class="text-green-400 font-semibold">Live monitoring is active. New items will appear below.</p>';
        }
    }

    async function handleResume() {
        const resumeBtn = document.getElementById('resume-btn');
        if (resumeBtn) {
            resumeBtn.disabled = true;
            resumeBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Resuming...';
        }
        try {
            await fetch('/resume-monitoring', { method: 'POST' });
            // The SSE 'resumed' status message will update the UI
        } catch (e) {
            console.error("Failed to resume monitoring:", e);
            // Re-enable button on failure
            if(resumeBtn) {
                resumeBtn.disabled = false;
                resumeBtn.innerHTML = '<i class="fa-solid fa-play mr-2"></i> Resume Monitoring';
            }
        }
    }

    async function handleSendToSlack(e) {
        const btn = e.currentTarget;
        const card = btn.closest('.feed-card');
        const itemData = JSON.parse(card.dataset.itemData);
        const webhookUrl = localStorage.getItem('slackWebhookUrl');

        if (!webhookUrl) {
            alert("Please save a Slack Webhook URL first.");
            return;
        }

        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Sending...';

        try {
            const response = await fetch('/send-to-slack', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item: itemData, webhookUrl: webhookUrl })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Failed to send to Slack');
            }
            
            btn.innerHTML = '<i class="fa-solid fa-check mr-1"></i> Sent!';
            // The button remains disabled to prevent re-sending.
        } catch (error) {
            console.error('Error sending to Slack:', error);
            btn.innerHTML = '<i class="fa-solid fa-xmark mr-1"></i> Failed';
            btn.classList.add('text-red-400');
            setTimeout(() => {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-brands fa-slack mr-1"></i> Send to Slack';
                btn.classList.remove('text-red-400');
            }, 3000);
        }
    }

    function connectToStream() {
        if (eventSource) return;
        eventSource = new EventSource('/stream');
        
        eventSource.onopen = () => {
            if (isLive) {
                ui.statusText.textContent = 'Live';
                ui.statusDot.classList.remove('bg-gray-500', 'bg-yellow-500', 'animate-pulse', 'bg-red-500');
                ui.statusDot.classList.add('bg-green-500');
            }
        };

        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'status') {
                handleStatusUpdate(data);
            } else {
                const noMatchesEl = document.getElementById('no-matches-placeholder');
                if (noMatchesEl) {
                    noMatchesEl.remove();
                }
                
                const item = data;
                if (document.querySelector(`[data-item-id="${item.id}"]`)) return;
                const card = createFeedCard(item);
                if (ui.controlsContainer.nextSibling) {
                    ui.controlsContainer.parentNode.insertBefore(card, ui.controlsContainer.nextSibling);
                } else {
                    ui.feedContainer.appendChild(card);
                }
            }
        };

        eventSource.onerror = () => {
            ui.statusText.textContent = 'Connection Lost';
            ui.statusDot.classList.remove('bg-green-500');
            ui.statusDot.classList.add('bg-red-500', 'animate-pulse');
            eventSource.close();
            eventSource = null;
            setTimeout(connectToStream, 5000);
        };
    }

    async function handleStartLive() {
        isLive = true;
        ui.startLiveBtn.disabled = true;
        ui.scanHistoricalBtn.disabled = true;
        ui.startLiveBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Starting...';
        await fetch('/start-monitoring', { method: 'POST' });
        connectToStream();
        ui.controlsContainer.innerHTML = '<p class="text-green-400 font-semibold">Live monitoring is active. New items will appear below.</p>';
    }

    async function handleScanHistorical() {
        isLive = true;
        ui.startLiveBtn.disabled = true;
        ui.scanHistoricalBtn.disabled = true;
        ui.scanHistoricalBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Scanning...';
        connectToStream();
        await fetch('/scan-historical?days=7', { method: 'POST' });
        ui.statusText.textContent = 'Scanning...';
        ui.statusDot.classList.remove('bg-gray-500', 'bg-green-500');
        ui.statusDot.classList.add('bg-yellow-500', 'animate-pulse');
        ui.controlsContainer.innerHTML = '<p class="text-yellow-400 font-semibold">Historical scan initiated. New items will appear as they are found.</p>';
    }

    // --- Initial Load ---
    async function initialize() {
        loadWebhookUrl();
        await fetchPatterns();
        try {
            const response = await fetch('/initial-items');
            const items = await response.json();
            renderInitialItems(items);
        } catch (e) {
            console.error("Failed to fetch initial items:", e);
            ui.placeholder.innerHTML = `
                <i class="fas fa-exclamation-triangle fa-3x text-red-500"></i>
                <p class="mt-4 text-lg">Could not fetch initial items.</p>
                <p class="text-sm">Please check the server logs and refresh the page.</p>
            `;
        }
    }
    
    // Setup main event listeners
    ui.saveWebhookBtn.addEventListener('click', saveWebhookUrl);
    ui.listenerForm.addEventListener('submit', handleSaveListener);
    ui.startLiveBtn.addEventListener('click', handleStartLive);
    ui.scanHistoricalBtn.addEventListener('click', handleScanHistorical);
    ui.showAddListenerModalBtn.addEventListener('click', () => openListenerModal('add'));
    ui.cancelListenerBtn.addEventListener('click', closeListenerModal);
    ui.listenerModal.addEventListener('click', (e) => {
        if (e.target === ui.listenerModal) closeListenerModal();
    });
    
    initialize();
});
</script>
</body>
</html>
"""

if __name__ == '__main__':
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', 5001))
    
    # Initialize patterns from .env or defaults
    try:
        initial_patterns = json.loads(SEARCH_PATTERNS_JSON)
        update_search_patterns(initial_patterns)
    except (json.JSONDecodeError, KeyError) as e:
        logging.error(f"FATAL: Invalid format for SEARCH_PATTERNS. Using empty list. Error: {e}")
        update_search_patterns([])

    # The monitoring thread is now started on-demand by the user via the UI.
    
    print("--- The-Eye-Of-Sauron 👁️ ---")
    print(f"🚀 Starting server at http://{host}:{port}")
    print(f"🔧 Monitoring API: {API_MAX_ITEM_URL}")
    print("👉 Open the URL in your browser to get started!")
    print("--------------------------------")
    app.run(host=host, port=port, debug=False, threaded=True)
