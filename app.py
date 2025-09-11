#!/usr/bin/env python3

import os
import sys
import logging
import json
import re
import queue
import threading
import concurrent.futures
import time
from datetime import datetime
from dotenv import load_dotenv
from openai import AzureOpenAI
from flask import Flask, Response, request, jsonify, render_template
import requests
from functools import reduce
import operator
import voyageai
from pymongo import MongoClient, DESCENDING, ASCENDING
from pymongo.errors import ConnectionFailure, OperationFailure, ConfigurationError

# --- Initialization & Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

# --- MongoDB Atlas Configuration ---
MDB_URI = os.getenv("MDB_URI")
mongo_client = None
# For Logging & Analytics
log_collection = None
daily_stats_collection = None
# For Hybrid Search Content
content_collection = None

# --- Hybrid Search Configuration ---
DB_NAME_LOGS = "sauron_logs"
DB_NAME_CONTENT = "sauron_content"
CONTENT_COLL_NAME = "feed_items"
TEXT_INDEX_NAME = "feed_items_text_index"
VECTOR_INDEX_NAME = "feed_items_vector_index"

if MDB_URI:
    try:
        # Set a connection timeout to avoid long waits
        mongo_client = MongoClient(MDB_URI, serverSelectionTimeoutMS=5000)
        # The ismaster command is cheap and does not require auth, used to validate the connection.
        mongo_client.admin.command('ismaster')
        
        # Setup Logging & Analytics Database
        logs_db = mongo_client[DB_NAME_LOGS]

        # --- Time-Series Collection Setup for Event Logging ---
        # Automatically create and use an optimized time-series collection for event logging,
        # which will improve performance and reduce storage.
        log_coll_name = "events"
        try:
            if log_coll_name not in logs_db.list_collection_names():
                timeseries_options = {
                    'timeField': 'timestamp',  # The field for the date in each document
                    'metaField': 'details',     # The field for metadata
                    'granularity': 'minutes'  # Optimizes data storage
                }
                logs_db.create_collection(log_coll_name, timeseries=timeseries_options)
                logging.info(f"✅ Created '{log_coll_name}' as an optimized time-series collection.")
            else:
                # If collection exists, verify it's a time-series collection
                coll_info_list = list(logs_db.list_collections(filter={'name': log_coll_name}))
                if coll_info_list and coll_info_list[0].get('type') == 'timeseries':
                    logging.info(f"✅ Verified '{log_coll_name}' is an existing time-series collection.")
                else:
                    logging.warning(f"⚠️ Collection '{log_coll_name}' exists but is not a time-series collection. For optimal performance, consider migrating it.")
        except OperationFailure as e:
            logging.error(f"❌ Could not create time-series collection '{log_coll_name}': {e}. Using standard collection as fallback.")
        except Exception as e:
            logging.error(f"❌ An unexpected error occurred during time-series setup: {e}")

        log_collection = logs_db.events
        # Note: An index on the `timeField` is automatically created for time-series collections.
        daily_stats_collection = logs_db.daily_stats
        
        # Setup Hybrid Search Content Database
        content_db = mongo_client[DB_NAME_CONTENT]
        content_collection = content_db[CONTENT_COLL_NAME]
        
        logging.info("✅ Successfully connected to MongoDB Atlas for all features.")
    except (ConnectionFailure, ConfigurationError) as e:
        logging.error(f"❌ Could not connect to MongoDB Atlas: {e}. All database features will be disabled.")
        mongo_client = None
        log_collection = None
        daily_stats_collection = None
        content_collection = None
else:
    logging.info("ℹ️ MDB_URI not set. Analytics and Hybrid Search will be disabled.")


# --- Flask App Initialization ---
app = Flask(__name__)
update_queue = queue.Queue()
is_paused_due_to_rate_limit = threading.Event()
is_manually_paused = threading.Event()
is_scan_cancelled = threading.Event()
# Add these two lines for deduplication
processed_ids_lock = threading.Lock()
processed_ids_this_session = set()

# --- Service Configuration ---
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 5))
PAGES_PER_SCAN = 10

# We now use two separate thread pools to prevent slow AI tasks
# from blocking fast API scanning tasks.
MAX_AI_WORKERS = int(os.getenv("MAX_AI_WORKERS", 5)) # A smaller, dedicated pool for LLM calls
global_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='SauronScanner')
ai_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_AI_WORKERS, thread_name_prefix='SauronAIWorker')


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
    
])
API_SOURCES = {}
sources_lock = threading.Lock()

# --- API Source Template Configuration ---
API_SOURCE_TEMPLATES = [
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
    },
    {
        "id": "hn-story-search",
        "name": "Hacker News Story Search",
        "description": "Searches for stories on Hacker News by date.",
        "variables": [
            {"name": "Query", "key": "{QUERY}", "placeholder": "e.g., ai"}
        ],
        "config": {
            "name": "Hacker News '{QUERY}' Stories",
            "apiUrl": "https://hn.algolia.com/api/v1/search_by_date?query={QUERY}&tags=story&page={PAGE}",
            "dataRoot": "hits",
            "fieldMappings": {
                "id": "objectID", "title": "title", "url": "url",
                "text": "story_text", "by": "author", "time": "created_at"
            },
            "fieldsToCheck": ["title", "story_text"]
        }
    },
    {
        "id": "hn-comment-search",
        "name": "Hacker News Comment Search",
        "description": "Searches for comments on Hacker News by date.",
        "variables": [
            {"name": "Query", "key": "{QUERY}", "placeholder": "e.g., mongodb"}
        ],
        "config": {
            "name": "Hacker News '{QUERY}' Comments",
            "apiUrl": "https://hn.algolia.com/api/v1/search_by_date?query={QUERY}&tags=comment&page={PAGE}",
            "dataRoot": "hits",
            "fieldMappings": {
                "id": "objectID", "title": "story_title", "url": "story_url",
                "text": "comment_text", "by": "author", "time": "created_at"
            },
            "fieldsToCheck": ["story_title", "comment_text"]
        }
    }
]

# --- Common Request Headers ---
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
}

# --- GitHub Personal Access Token (PAT) Configuration ---
GITHUB_PAT = os.getenv("GITHUB_PAT")
if GITHUB_PAT:
    logging.info("✅ GitHub PAT found. Authenticated requests will be used for GitHub APIs.")
else:
    logging.warning("⚠️ GitHub PAT not found. Using unauthenticated requests, which may lead to rate limiting.")

# --- AI & Embedding Client Configuration ---

# Chat Client is always Azure OpenAI for now
azure_client = None
CHAT_DEPLOYMENT = None
try:
    AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")

    if not all([AZURE_ENDPOINT, AZURE_KEY]):
        raise ValueError("Azure OpenAI env vars (ENDPOINT, API_KEY) are required for AI summary features.")
        
    azure_client = AzureOpenAI(
        api_version="2024-02-01",
        azure_endpoint=AZURE_ENDPOINT,
        api_key=AZURE_KEY,
    )
    logging.info(f"✅ Azure OpenAI client configured for Chat Summaries: '{CHAT_DEPLOYMENT}'.")
except Exception as e:
    logging.error(f"❌ Error initializing Azure OpenAI client for chat: {e}. Summary features will be disabled.")
    azure_client = None

# Embedding Client is configurable (Azure default)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "azure").lower()
EMBEDDING_DIMENSIONS = None
voyage_client = None
# These will be set based on the provider
VOYAGE_EMBEDDING_MODEL = None
EMBEDDING_DEPLOYMENT = None

logging.info(f"ℹ️ Using '{EMBEDDING_PROVIDER}' as the embedding provider.")

if EMBEDDING_PROVIDER == "azure":
    if azure_client:
        EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
        EMBEDDING_DIMENSIONS = int(os.getenv("AZURE_EMBEDDING_DIMENSIONS", 1536))
        logging.info(f"-> Azure Embeddings: '{EMBEDDING_DEPLOYMENT}' ({EMBEDDING_DIMENSIONS} dimensions).")
    else:
        logging.error("❌ EMBEDDING_PROVIDER is 'azure' but Azure client failed to initialize. Embeddings disabled.")

elif EMBEDDING_PROVIDER == "voyageai":
    try:
        VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
        if not VOYAGE_API_KEY:
            raise ValueError("VOYAGE_API_KEY environment variable is required when EMBEDDING_PROVIDER is 'voyageai'.")
        
        voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
        VOYAGE_EMBEDDING_MODEL = os.getenv("VOYAGE_EMBEDDING_MODEL", "voyage-3.5-lite")
        EMBEDDING_DIMENSIONS = int(os.getenv("VOYAGE_EMBEDDING_DIMENSIONS", 1024))
        logging.info(f"-> Voyage AI Embeddings: '{VOYAGE_EMBEDDING_MODEL}' ({EMBEDDING_DIMENSIONS} dimensions).")
    except Exception as e:
        logging.error(f"❌ Error initializing Voyage AI client: {e}. Embedding features will be disabled.")
        voyage_client = None
        EMBEDDING_DIMENSIONS = None

else:
    logging.error(f"❌ Invalid EMBEDDING_PROVIDER: '{EMBEDDING_PROVIDER}'. Must be 'azure' or 'voyageai'. Embeddings disabled.")

# Final sanity check
if EMBEDDING_DIMENSIONS is None:
    logging.warning("⚠️ Vector embedding generation is DISABLED due to configuration errors.")


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

# --- Hybrid Search & Index Management ---
def get_embedding(text: str) -> list[float]:
    """Generate vector embedding for a text string using the configured provider."""
    if EMBEDDING_PROVIDER == "azure":
        if not azure_client: return []
        try:
            return azure_client.embeddings.create(input=[text], model=EMBEDDING_DEPLOYMENT).data[0].embedding
        except Exception as e:
            logging.error(f"Failed to generate Azure embedding: {e}")
            return []

    elif EMBEDDING_PROVIDER == "voyageai":
        if not voyage_client: return []
        try:
            # Voyage AI's embed function expects a list of texts
            result = voyage_client.embed(texts=[text], model=VOYAGE_EMBEDDING_MODEL, input_type="document")
            return result.embeddings[0]
        except Exception as e:
            logging.error(f"Failed to generate Voyage AI embedding: {e}")
            return []
            
    else:
        # This case should ideally not be hit if config is correct, but it's a good safeguard.
        logging.warning(f"Embedding generation skipped: provider '{EMBEDDING_PROVIDER}' is not configured correctly.")
        return []

def wait_for_index(coll, index_name: str, timeout: int = 300):
    """Poll search indexes until the specified index is ready."""
    logging.info(f"⏳ Waiting for index '{index_name}' to be ready...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            indexes = list(coll.list_search_indexes(name=index_name))
            if indexes and (indexes[0].get('status') == 'READY' or indexes[0].get('queryable') == True):
                logging.info(f"✅ Index '{index_name}' is ready.")
                return True
            time.sleep(5)
        except OperationFailure:
            time.sleep(5)  # Index might not be queryable yet during creation
    raise TimeoutError(f"Index '{index_name}' did not become ready in {timeout}s.")

def create_hybrid_search_indexes():
    """
    Checks for and creates the required text and vector indexes in the background.
    Automatically handles vector index recreation if embedding dimensions change.
    """
    if content_collection is None:
        logging.warning("DB not available, skipping index creation.")
        return
    if EMBEDDING_DIMENSIONS is None:
        logging.warning("EMBEDDING_DIMENSIONS not set, skipping vector index creation.")
        return

    try:
        # Ensure the collection exists before creating an index
        db = mongo_client[DB_NAME_CONTENT]
        if CONTENT_COLL_NAME not in db.list_collection_names():
            db.create_collection(CONTENT_COLL_NAME)
            logging.info(f"✅ Collection '{CONTENT_COLL_NAME}' did not exist and was created.")

        # Get all search indexes as a dictionary for easy lookup
        existing_indexes_details = list(content_collection.list_search_indexes())
        existing_indexes = {idx['name']: idx for idx in existing_indexes_details}
        
        # 1. Create Text Search Index
        if TEXT_INDEX_NAME not in existing_indexes:
            logging.info(f"🛠️ Creating text index: '{TEXT_INDEX_NAME}'...")
            text_index_model = { "name": TEXT_INDEX_NAME, "definition": { "mappings": { "dynamic": True } } }
            content_collection.create_search_index(model=text_index_model)
            wait_for_index(content_collection, TEXT_INDEX_NAME)
        else:
            logging.info(f"ℹ️ Text index '{TEXT_INDEX_NAME}' already exists.")

        # 2. FIX: Create or Recreate Vector Search Index with dimension check
        recreate_vector_index = False
        if VECTOR_INDEX_NAME in existing_indexes:
            index_details = existing_indexes[VECTOR_INDEX_NAME]
            # Safely get the definition. It might be None if the index is still pending.
            # The correct key from the API is 'latestDefinition'.
            index_def = index_details.get('latestDefinition')

            if index_def:
                try:
                    # Now that we know index_def exists, we can safely access its nested keys.
                    existing_dims = index_def['mappings']['fields']['content_embedding']['dimensions']
                    if existing_dims != EMBEDDING_DIMENSIONS:
                        logging.warning(
                            f"⚠️ Vector index '{VECTOR_INDEX_NAME}' dimension mismatch! "
                            f"Index has {existing_dims}, but config requires {EMBEDDING_DIMENSIONS}. Recreating index."
                        )
                        content_collection.drop_search_index(VECTOR_INDEX_NAME)
                        recreate_vector_index = True
                    else:
                        logging.info(f"ℹ️ Vector index '{VECTOR_INDEX_NAME}' already exists and dimensions match.")
                except KeyError as e:
                    # This handles cases where the definition exists but has an unexpected structure.
                    logging.warning(f"Could not parse the structure of existing vector index '{VECTOR_INDEX_NAME}'. Recreating it. Error: Missing key {e}")
                    content_collection.drop_search_index(VECTOR_INDEX_NAME)
                    recreate_vector_index = True
            else:
                # This handles the original problem: the index exists but its definition isn't available yet.
                logging.warning(f"Existing vector index '{VECTOR_INDEX_NAME}' found but its definition is not yet available (likely pending). Recreating it to be safe.")
                content_collection.drop_search_index(VECTOR_INDEX_NAME)
                recreate_vector_index = True
        else:
            # Index does not exist at all
            recreate_vector_index = True

        if recreate_vector_index:
            logging.info(f"🛠️ Creating vector index '{VECTOR_INDEX_NAME}' with {EMBEDDING_DIMENSIONS} dimensions...")
            vector_index_model = {
                "name": VECTOR_INDEX_NAME,
                "definition": { "mappings": { "fields": { "content_embedding": {
                    "type": "knnVector",
                    "dimensions": EMBEDDING_DIMENSIONS,
                    "similarity": "cosine"
                }}}}
            }
            content_collection.create_search_index(model=vector_index_model)
            wait_for_index(content_collection, VECTOR_INDEX_NAME)
            
    except (OperationFailure, TimeoutError, Exception) as e:
        logging.error(f"❌ Failed to create or verify search indexes: {e}")

# --- Core Application Logic ---
def log_event_and_update_stats(event_type, details):
    """Logs an event and updates stats, falling back to client-side localStorage if MongoDB is disabled."""
    if log_collection is None or daily_stats_collection is None:
        update_queue.put({"type": "local_analytics_update", "eventType": event_type, "details": details})
        return

    now = datetime.utcnow()
    try:
        log_collection.insert_one({"timestamp": now, "eventType": event_type, "details": details})
    except Exception as e:
        logging.error(f"Failed to log to events collection: {e}")
    
    try:
        date_str = now.strftime('%Y-%m-%d')
        hour_str = str(now.hour)
        inc_op = {}

        if event_type == 'scan_started':
            source_name_safe = details.get('sourceName', 'Unknown').replace('.', '_').replace('$', '_')
            inc_op = {'totalScansStarted': 1, f'scansBySource.{source_name_safe}': 1}
        elif event_type == 'item_matched':
            source_name_safe = details.get('sourceName', 'Unknown').replace('.', '_').replace('$', '_')
            label_safe = details.get('matchedLabel', 'Unknown').replace('.', '_').replace('$', '_')
            inc_op = {
                'totalItemsMatched': 1, f'hourlyActivity.{hour_str}': 1,
                f'matchesByLabel.{label_safe}': 1, f'matchesBySourceLabel.{source_name_safe}.{label_safe}': 1
            }
        elif event_type == 'summary_generated' and details.get('success'):
            inc_op = {'totalSummariesGenerated': 1}
        
        if inc_op:
            daily_stats_collection.update_one(
                {'_id': date_str},
                {'$inc': inc_op, '$setOnInsert': {'_id': date_str, 'date': date_str}},
                upsert=True
            )
    except Exception as e:
        logging.error(f"Failed to update daily stats in MongoDB: {e}, full error: {getattr(e, 'details', {})}")

def get_nested_value(data_dict, key_string):
    """Safely retrieves a value from a nested dictionary using dot notation."""
    if not key_string: return data_dict
    try:
        return reduce(operator.getitem, key_string.split('.'), data_dict)
    except (KeyError, TypeError, IndexError):
        return None

def get_llm_summary(prompt_text):
    """Generates a summary using the configured Azure OpenAI chat model."""
    if not azure_client: return "[Error: OpenAI client not configured]"
    if is_manually_paused.is_set():
        logging.warning("LLM request cancelled due to manual pause.")
        return "[Status: Paused. Request cancelled.]"
    if is_paused_due_to_rate_limit.is_set():
        logging.warning("LLM request blocked due to rate limit pause.")
        return "[Status: Paused due to rate limit. Request not sent.]"
    try:
        response = azure_client.chat.completions.create(
            model=CHAT_DEPLOYMENT,
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
    """
    Processes a matched item. It immediately sends a 'pending' card to the UI,
    then submits the slow summary/embedding task to a separate AI worker thread pool.
    """
    mappings = source_config.get('fieldMappings', {})
    item_id_from_source = get_nested_value(item, mappings.get('id'))
    if not item_id_from_source:
        logging.warning(f"Could not extract a unique ID from an item in '{source_config['name']}'. Skipping.")
        return
        
    unique_item_id = f"{source_config['name']}-{item_id_from_source}"

    with processed_ids_lock:
        if unique_item_id in processed_ids_this_session:
            return
        processed_ids_this_session.add(unique_item_id)

    if content_collection is not None:
        existing_doc = content_collection.find_one({'_id': unique_item_id})
        if existing_doc and existing_doc.get('ai_summary') and not existing_doc['ai_summary'].startswith(("[Error:", "[Status:")):
            logging.info(f"🔁 Item {unique_item_id} already has a valid summary in DB. Using cached result.")
            time_from_doc = existing_doc.get('time')
            unix_timestamp = int(time_from_doc.timestamp()) if isinstance(time_from_doc, datetime) else 0

            # --- FIX: Use data from the database for consistency ---
            item_data = {
                "id": unique_item_id, "type": "api_item", "source_name": source_config['name'],
                "by": existing_doc.get('by'), "time": unix_timestamp,
                "title": existing_doc.get('title'), "url": existing_doc.get('url'),
                "text": existing_doc.get('text'),  # <-- Use the stored text
                "matched_label": matched_label, "processed_at": datetime.utcnow().isoformat(),
                "summary_status": "complete"
            }
            update_queue.put(item_data)
            update_queue.put({"type": "summary_update", "id": unique_item_id, "ai_summary": existing_doc['ai_summary']})
            return
            
    # --- Step 1: Immediately send the 'pending' card to the UI ---
    logging.info(f"✅ NEW/UNSUMMARIZED: Item '{unique_item_id}' matched '{matched_label}'. Queueing for summary.")
    log_event_and_update_stats("item_matched", {
        "sourceName": source_config.get('name'), "matchedLabel": matched_label, "itemId": str(item_id_from_source)
    })

    # --- Extract core fields from the item ---
    title_val = get_nested_value(item, mappings.get('title'))
    url_val = get_nested_value(item, mappings.get('url'))
    by_val = get_nested_value(item, mappings.get('by'))
    text_val = get_nested_value(item, mappings.get('text'))
    time_str = str(get_nested_value(item, mappings.get('time', '')))

    # --- FIX: If URL is missing, construct a fallback for specific sources like Hacker News ---
    if not url_val:
        source_name = source_config.get('name', '')
        if 'Hacker News' in source_name:
            object_id = get_nested_value(item, mappings.get('id'))
            if object_id:
                url_val = f"https://news.ycombinator.com/item?id={object_id}"
                logging.info(f"Constructed fallback URL for HN item {object_id}: {url_val}")

    # --- Parse timestamp ---
    unix_timestamp = 0
    if time_str:
        try:
            if time_str.isdigit(): unix_timestamp = int(time_str)
            else:
                time_str_cleaned = time_str.replace('Z', '+00:00')
                if '.' in time_str_cleaned:
                    parts = time_str_cleaned.split('.')
                    if len(parts) > 1 and len(parts[1]) > 6:
                        time_str_cleaned = parts[0] + '.' + parts[1][:6]
                unix_timestamp = int(datetime.fromisoformat(time_str_cleaned).timestamp())
        except (ValueError, TypeError) as e:
            logging.warning(f"Could not parse timestamp '{time_str}': {e}")

    # --- Prepare and queue the initial item data for the UI ---
    item_data = {
        "id": unique_item_id, "type": "api_item", "source_name": source_config['name'],
        "by": by_val, "time": unix_timestamp, "title": title_val, "url": url_val,
        "text": text_val, "matched_label": matched_label,
        "processed_at": datetime.utcnow().isoformat(), "summary_status": "pending"
    }
    update_queue.put(item_data)

    # --- Step 2: Define the slow work and submit it to the AI worker pool ---
    def generate_summary_and_update():
        """This function runs in the separate `ai_executor` pool."""
        if is_manually_paused.is_set() or is_scan_cancelled.is_set():
            logging.info(f"Summary generation for item {item_data['id']} halted due to pause/cancel.")
            with processed_ids_lock:
                processed_ids_this_session.discard(unique_item_id)
            return

        content_to_summarize = f"Title: {item_data['title']}\n\nContent:\n{item_data['text']}"
        prompt_text = (f"## Matched Keyword\n`{matched_label}`\n\n"
                       f"## API Content to Analyze (from {source_config['name']})\n\n{content_to_summarize}")
        
        ai_summary = get_llm_summary(prompt_text)
        
        summary_successful = not ai_summary.startswith(("[Error:", "[Status:"))
        log_event_and_update_stats("summary_generated", {
            "sourceName": source_config.get('name'), "itemId": item_data["id"],
            "success": summary_successful, "error": ai_summary if not summary_successful else None
        })

        if summary_successful and content_collection is not None:
            text_to_embed = f"Title: {item_data['title']}\nSummary: {ai_summary}"
            embedding = get_embedding(text_to_embed)
            
            # --- FIX: Save the original text to the database ---
            doc_to_store = {
                "title": item_data['title'], 
                "url": item_data['url'], 
                "by": item_data['by'],
                "time": datetime.fromtimestamp(item_data['time']), 
                "source_name": item_data['source_name'],
                "ai_summary": ai_summary,
                "text": item_data['text']  # <-- Save the original text
            }

            if embedding:
                doc_to_store["content_embedding"] = embedding
            try:
                content_collection.update_one({'_id': item_data['id']}, {'$set': doc_to_store}, upsert=True)
                logging.info(f"📝 Stored item {item_data['id']} for hybrid search.")
            except Exception as e:
                logging.error(f"❌ Failed to store item {item_data['id']} for hybrid search: {e}")
        
        update_queue.put({"type": "summary_update", "id": item_data["id"], "ai_summary": ai_summary})

    ai_executor.submit(generate_summary_and_update)


def check_if_item_matches(item, source_config):
    if not item: return None
    content_to_check = " ".join([str(get_nested_value(item, field) or '') for field in source_config.get('fieldsToCheck', [])])
    with patterns_lock: current_patterns = SEARCH_PATTERNS.copy()
    for label, pattern in current_patterns.items():
        if pattern.search(content_to_check): return label
    return None

def perform_api_scan(source_config, start_page=1):
    source_name = source_config.get('name', 'Unknown Source')
    update_queue.put({"type": "status", "status": "scanning", "reason": f"Starting scan for {source_name}...", "source_name": source_name})
    logging.info(f"API SCAN: Starting for source '{source_name}' from page {start_page}...")
    log_event_and_update_stats("scan_started", {"sourceName": source_name, "startPage": start_page})

    api_url_template = source_config.get('apiUrl')
    if not api_url_template:
        update_queue.put({"type": "status", "status": "error", "reason": f"Missing 'apiUrl' in config for {source_name}"})
        return

    is_zero_indexed = source_config.get("paginationZeroIndexed", False)
    current_page = start_page
    try:
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

            page_to_request = current_page - 1 if is_zero_indexed else current_page
            api_url = api_url_template.replace("{PAGE}", str(page_to_request))
            logging.info(f"Fetching page {current_page} (API page {page_to_request}) from {source_name} ({api_url})...")
            update_queue.put({"type": "status", "status": "scanning", "reason": f"Fetching page {current_page} from {source_name}...", "source_name": source_name})

            request_headers = HEADERS.copy()
            if "api.github.com" in api_url:
                request_headers["Accept"] = "application/vnd.github.v3+json"
                if GITHUB_PAT:
                    request_headers["Authorization"] = f"Bearer {GITHUB_PAT}"

            response = requests.get(api_url, headers=request_headers, timeout=20)
            response.raise_for_status()

            try: data = response.json()
            except json.JSONDecodeError:
                logging.error(f"Failed to decode JSON from {api_url}")
                update_queue.put({"type": "status", "status": "error", "reason": f"Could not parse response from {source_name}."})
                return

            items = get_nested_value(data, source_config.get('dataRoot'))

            if not isinstance(items, list) or not items:
                logging.info(f"No more items found for source '{source_name}'.")
                update_queue.put({"type": "status", "status": "idle", "reason": f"Scan of {source_name} complete."})
                log_event_and_update_stats("scan_completed", {"sourceName": source_name, "reason": "no_more_items", "pagesScanned": i})
                return

            for item in items:
                if is_scan_cancelled.is_set(): break
                if matched_label := check_if_item_matches(item, source_config):
                    # Submit item processing to the global executor for parallel handling
                    global_executor.submit(process_and_queue_api_item, item, matched_label, source_config)
            
            if is_scan_cancelled.is_set(): break
            current_page += 1

        if is_scan_cancelled.is_set():
            logging.info(f"Scan for {source_name} was cancelled by user.")
            update_queue.put({"type": "status", "status": "idle", "reason": f"Scan for {source_name} cancelled."})
            log_event_and_update_stats("scan_cancelled", {"sourceName": source_name})
            return

        total_pages_scanned = current_page - 1
        logging.info(f"Scan paused. Total pages scanned for {source_name}: {total_pages_scanned}.")
        update_queue.put({
            "type": "status",
            "status": "scan_paused",
            "source_name": source_name,
            "reason": f"Paused after scanning {total_pages_scanned} total pages.",
            "next_page": current_page
        })
        log_event_and_update_stats("scan_paused_limit", {"sourceName": source_name, "nextPage": current_page})

    except requests.RequestException as e:
        logging.error(f"API SCAN: Failed to fetch from {source_name}: {e}")
        update_queue.put({"type": "status", "status": "error", "reason": f"Failed to fetch data: {e}"})
        log_event_and_update_stats("scan_error", {"sourceName": source_name, "error": str(e)})
    except Exception as e:
        logging.error(f"API SCAN: An unexpected error occurred: {e}", exc_info=True)
        update_queue.put({"type": "status", "status": "error", "reason": f"An unexpected error occurred: {e}"})
        log_event_and_update_stats("scan_error", {"sourceName": source_name, "error": str(e)})

# --- Flask Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/stream')
def stream():
    def event_stream():
        while True:
            try:
                data = update_queue.get(timeout=None)
                yield f"data: {json.dumps(data)}\n\n"
            except queue.Empty: continue
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
        log_event_and_update_stats("config_update", {"configType": "patterns", "count": len(SEARCH_PATTERNS)})

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
    if pattern is None: return jsonify({"valid": False, "error": "No pattern provided."}), 400
    try:
        re.compile(pattern)
        return jsonify({"valid": True})
    except re.error as e:
        return jsonify({"valid": False, "error": str(e)})

def update_api_sources(sources_list):
    global API_SOURCES
    new_sources = {}
    for source in sources_list:
        if 'name' in source: new_sources[source['name']] = source
    with sources_lock:
        API_SOURCES = new_sources
        logging.info(f"Updated API Sources. Now have {len(API_SOURCES)} sources configured.")
        log_event_and_update_stats("config_update", {"configType": "api_sources", "count": len(API_SOURCES)})

@app.route('/api-sources', methods=['GET', 'POST'])
def manage_api_sources():
    if request.method == 'GET':
        with sources_lock: return jsonify(list(API_SOURCES.values()))
    if request.method == 'POST':
        new_sources_list = request.get_json()
        if not isinstance(new_sources_list, list): return jsonify({"error": "Invalid data format"}), 400
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
    is_manually_paused.clear(); is_scan_cancelled.clear()

    if not source_name: return jsonify({"error": "Missing 'source_name' in request body"}), 400
    with sources_lock: source_config = API_SOURCES.get(source_name)
    if not source_config: return jsonify({"error": f"Source '{source_name}' not found."}), 404

    global_executor.submit(perform_api_scan, source_config, start_page)
    return jsonify({"status": f"API scan for source '{source_name}' initiated."})

@app.route('/scan-all-sources', methods=['POST'])
def scan_all_sources():
    """Initiates a scan for sources specified by the client."""
    is_manually_paused.clear()
    is_scan_cancelled.clear()
    
    data = request.get_json()
    source_names_to_scan = data.get('source_names')

    if not source_names_to_scan or not isinstance(source_names_to_scan, list):
        return jsonify({"error": "A list of 'source_names' to scan must be provided."}), 400
    
    with sources_lock:
        # Filter the global sources to only include the ones requested by the client
        sources_to_scan = [
            API_SOURCES[name] for name in source_names_to_scan if name in API_SOURCES
        ]

    if not sources_to_scan:
        return jsonify({"error": "None of the provided source names were found or enabled."}), 404

    logging.info(f"Initiating parallel scan for {len(sources_to_scan)} requested sources.")
    for source_config in sources_to_scan:
        global_executor.submit(perform_api_scan, source_config, 1)

    return jsonify({"status": f"{len(sources_to_scan)} source scans initiated in parallel."})


@app.route('/generate-summary', methods=['POST'])
def generate_summary():
    data = request.get_json()
    if not data or not all(k in data for k in ['title', 'text', 'matched_label', 'source_name']):
        return jsonify({"error": "Missing required data for summary generation."}), 400

    content_to_summarize = f"Title: {data['title']}\n\nContent:\n{data['text']}"
    prompt_text = (f"## Matched Keyword\n`{data['matched_label']}`\n\n"
                   f"## API Content to Analyze (from {data['source_name']})\n\n{content_to_summarize}")
    
    summary = get_llm_summary(prompt_text)
    return jsonify({"ai_summary": summary})

# --- HYBRID SEARCH ENDPOINT ---
@app.route('/hybrid-search', methods=['POST'])
def hybrid_search():
    if content_collection is None:
        return jsonify({"error": "Database not configured for search."}), 503

    data = request.get_json()
    query = data.get('query')
    if not query:
        return jsonify({"error": "Missing 'query' in request body"}), 400

    try:
        query_embedding = get_embedding(query)
        if not query_embedding:
            return jsonify({"error": "Failed to generate query embedding."}), 500

        pipeline = [
            {
                "$rankFusion": {
                    "input": {
                        "pipelines": {
                            "vectorPipeline": [
                                {
                                    "$vectorSearch": {
                                        "index": VECTOR_INDEX_NAME,
                                        "path": "content_embedding",
                                        "queryVector": query_embedding,
                                        "numCandidates": 150,
                                        "limit": 20
                                    }
                                }
                            ],
                            "fullTextPipeline": [
                                {
                                    "$search": {
                                        "index": TEXT_INDEX_NAME,
                                        "text": {
                                            "query": query,
                                            "path": ["title", "ai_summary"]
                                        }
                                    }
                                },
                                { "$limit": 20 }
                            ]
                        }
                    },
                    "combination": {
                        "weights": {
                            "vectorPipeline": 0.7,
                            "fullTextPipeline": 0.3
                        }
                    },
                    "scoreDetails": True
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "id": "$_id",
                    "title": 1,
                    "url": 1,
                    "ai_summary": 1,
                    "source_name": 1,
                    "scoreDetails": {"$meta": "scoreDetails"}
                }
            },
            {
                "$addFields": {
                    "score": "$scoreDetails.value"
                }
            },
            { "$limit": 10 }
        ]
        results = list(content_collection.aggregate(pipeline))
        return jsonify(results)

    except OperationFailure as e:
        logging.error(f"Hybrid search failed: {e.details}")
        return jsonify({"error": f"An error occurred during the search operation: {e.details}"}), 500
    except Exception as e:
        logging.error(f"An unexpected error occurred in hybrid search: {e}")
        return jsonify({"error": "An unexpected server error occurred."}), 500

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

        slack_payload = { "blocks": [
            { "type": "header", "text": { "type": "plain_text", "text": f"New '{item.get('matched_label', 'N/A')}' Mention from {source_name}"}},
            { "type": "section", "text": { "type": "mrkdwn", "text": f"*<{item_url}|{title}>*" if item_url else f"*{title}*"}},
            { "type": "section", "text": { "type": "mrkdwn", "text": f"*AI Summary:*\n> {formatted_summary}"}},
            { "type": "context", "elements": [
                {"type": "mrkdwn", "text": f"*Author:* `{author}`"}, {"type": "mrkdwn", "text": f"*Posted:* {post_time}"},
                {"type": "mrkdwn", "text": f"*Source:* <{item_url}|View Source>" if item_url else f"*Source:* {source_name}"}
            ]},
            {"type": "divider"},
            { "type": "context", "elements": [{"type": "mrkdwn", "text": "Sent from The-Eye-Of-Sauron 👁️"}]}
        ]}
        response = requests.post(webhook_url, json=slack_payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Successfully sent item {item.get('id')} to Slack.")
        log_event_and_update_stats("slack_notification_sent", {"itemId": item.get('id'), "success": True})
        return jsonify({"status": "success"})
    except requests.RequestException as e:
        logging.error(f"Error sending to Slack: {e}")
        log_event_and_update_stats("slack_notification_failed", {"itemId": item.get('id'), "success": False, "error": str(e)})
        return jsonify({"error": f"Failed to send to Slack: {e}"}), 500
    except Exception as e:
        logging.error(f"An unexpected error occurred in send_to_slack: {e}")
        return jsonify({"error": "An unexpected server error occurred."}), 500

@app.route('/preview-api-source', methods=['POST'])
def preview_api_source():
    data = request.get_json()
    api_url_template = data.get('apiUrl')
    if not api_url_template: return jsonify({"error": "Missing 'apiUrl'"}), 400
    api_url = api_url_template.replace("{PAGE}", "1")
    logging.info(f"Fetching preview from: {api_url}")

    try:
        request_headers = HEADERS.copy()
        if "api.github.com" in api_url:
            request_headers["Accept"] = "application/vnd.github.v3+json"
            if GITHUB_PAT:
                request_headers["Authorization"] = f"Bearer {GITHUB_PAT}"
        
        response = requests.get(api_url, headers=request_headers, timeout=15)
        response.raise_for_status()
        
        try: return jsonify(response.json())
        except json.JSONDecodeError: return jsonify({"error": "Response was not valid JSON.", "raw_response": response.text})

    except requests.exceptions.Timeout:
        return jsonify({"error": "Request timed out after 15 seconds."}), 504
    except requests.exceptions.HTTPError as e:
        error_body = e.response.text
        return jsonify({"error": f"HTTP Error: {e.response.status_code} {e.response.reason}", "raw_response": error_body}), e.response.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch data: {str(e)}"}), 500

# --- Analytics Route ---
@app.route('/analytics/daily-stats', methods=['GET'])
def get_daily_stats():
    if daily_stats_collection is None:
        return jsonify({"use_local_storage": True})
    date_str = request.args.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
    try:
        final_stats = {
            '_id': date_str, 'date': date_str, 'totalScansStarted': 0, 'totalItemsMatched': 0, 'totalSummariesGenerated': 0,
            'scansBySource': {}, 'matchesByLabel': {}, 'matchesBySourceLabel': {}, 'hourlyActivity': {str(h): 0 for h in range(24)}
        }
        stats_from_db = daily_stats_collection.find_one({'_id': date_str})
        if stats_from_db:
            final_stats.update({k: stats_from_db.get(k, v) for k, v in final_stats.items()})
            if 'hourlyActivity' in stats_from_db and isinstance(stats_from_db['hourlyActivity'], dict):
                final_stats['hourlyActivity'].update(stats_from_db['hourlyActivity'])
        return jsonify(final_stats)
    except Exception as e:
        logging.error(f"Failed to fetch daily stats: {e}")
        return jsonify({"error": "An error occurred while fetching analytics data."}), 500

@app.route('/matches', methods=['GET'])
def get_matches():
    """
    An endpoint to retrieve, filter, sort, and paginate through the stored matches (content items).
    
    Query Parameters:
    - page (int): The page number to retrieve. Default: 1.
    - per_page (int): The number of items per page. Default: 20, Max: 100.
    - sort_by (str): The field to sort by. Default: 'time'.
    - sort_order (str): The sort direction ('asc' or 'desc'). Default: 'desc'.
    - source_name (str): Filter results by specific source names. Can be provided multiple times.
    - query (str): A search term to filter results by title or AI summary.
    """
    if content_collection is None:
        return jsonify({"error": "Database not configured. This feature is unavailable."}), 503

    try:
        # --- 1. Parse and Validate Request Arguments ---
        page = request.args.get('page', 1, type=int)
        if page < 1: page = 1

        per_page = request.args.get('per_page', 20, type=int)
        # Add a reasonable cap to per_page to prevent abuse
        if per_page > 100: per_page = 100
        if per_page < 1: per_page = 1

        sort_by = request.args.get('sort_by', 'time', type=str)
        sort_order_str = request.args.get('sort_order', 'desc', type=str).lower()
        sort_direction = DESCENDING if sort_order_str == 'desc' else ASCENDING

        # --- 2. Build the MongoDB Filter Query ---
        query_filter = {}
        
        # Correctly handle multiple source_name parameters
        source_name_list = request.args.getlist('source_name')
        if source_name_list:
            # Use the '$in' operator to match documents where source_name is in the provided list
            query_filter['source_name'] = {'$in': source_name_list}

        # Filter by a text search query if provided
        search_query = request.args.get('query', type=str)
        if search_query:
            # Use regex for a simple, case-insensitive search on title and summary
            regex_pattern = re.compile(search_query, re.IGNORECASE)
            query_filter['$or'] = [
                {'title': {'$regex': regex_pattern}},
                {'ai_summary': {'$regex': regex_pattern}}
            ]
            
    
        # --- 3. Execute Queries for Data and Pagination ---
        
        # First, get the total count of documents that match the filter for pagination purposes
        total_items = content_collection.count_documents(query_filter)
        if total_items == 0:
            return jsonify({
                "pagination": {"page": page, "per_page": per_page, "total_items": 0, "total_pages": 0},
                "data": []
            })
        
        total_pages = (total_items + per_page - 1) // per_page # Integer division to get ceiling
        
        # Calculate the number of documents to skip
        skip_items = (page - 1) * per_page
        
        # Now, retrieve the paginated and sorted documents
        cursor = content_collection.find(query_filter)\
                                 .sort(sort_by, sort_direction)\
                                 .skip(skip_items)\
                                 .limit(per_page)

        # --- 4. Format the Results for JSON Response ---
        results = []
        for doc in cursor:
            # Convert non-serializable types to strings/numbers
            doc['id'] = str(doc['_id'])
            del doc['_id'] # Remove the original BSON ObjectId
            
            # Convert datetime to ISO 8601 string format
            if 'time' in doc and isinstance(doc.get('time'), datetime):
                doc['time'] = doc['time'].isoformat()
            
            # The 'content_embedding' can be large, so we exclude it from this general-purpose endpoint
            if 'content_embedding' in doc:
                del doc['content_embedding']

            results.append(doc)
            
        # --- 5. Return the Final Structured Response ---
        response_data = {
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_items": total_items,
                "total_pages": total_pages
            },
            "data": results
        }
        return jsonify(response_data)

    except OperationFailure as e:
        logging.error(f"Error fetching matches from database: {e.details}")
        return jsonify({"error": f"A database error occurred: {e.details}"}), 500
    except Exception as e:
        logging.error(f"An unexpected error occurred in get_matches: {e}")
        return jsonify({"error": "An unexpected server error occurred."}), 500

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

    # Start background thread to create search indexes if they don't exist
    if mongo_client:
        threading.Thread(target=create_hybrid_search_indexes, daemon=True).start()

    print("--- The-Eye-Of-Sauron 👁️  (Generic API Scanner) ---")
    print(f"🚀 Starting server at http://{host}:{port}")
    print("👉 Open the URL in your browser to get started!")
    print("--------------------------------------------------")
    app.run(host=host, port=port, debug=False, threaded=True)
