import os
import re
import json
import time
import random
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
 
from youtube_transcript_api import YouTubeTranscriptApi
try:
    from youtube_transcript_api import RequestBlocked, IpBlocked
except ImportError:
    RequestBlocked = None
    IpBlocked = None
try:
    from youtube_transcript_api.proxies import WebshareProxyConfig
except ImportError:
    WebshareProxyConfig = None
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import pandas as pd
 
# ---- SDKs for each provider ----
from groq import Groq
from groq import RateLimitError as GroqRateLimitError
from groq import APIError as GroqAPIError
from groq import APIConnectionError as GroqAPIConnectionError
 
from openai import OpenAI  # used for Mistral (OpenAI-compatible endpoint)
from openai import RateLimitError as OAIRateLimitError
from openai import APIError as OAIAPIError
from openai import APIConnectionError as OAIAPIConnectionError
 
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted as GeminiRateLimitError
from google.api_core.exceptions import ServiceUnavailable as GeminiServiceUnavailable
from google.api_core.exceptions import DeadlineExceeded as GeminiDeadlineExceeded
from google.api_core.exceptions import GoogleAPIError as GeminiAPIError
 
 
# ---- Input CSV ----
df = pd.read_csv("E:/Python/farmerproject/chhattisgarh_.mandi.csv")
records = df[['url', 'published_date']].to_dict('records')
 
 
# ---- CONFIG ----
GROQ_API_KEYS = [
    os.environ.get("GROQ_API_KEY_1", ""),
    os.environ.get("GROQ_API_KEY_2", ""),
    os.environ.get("GROQ_API_KEY_3", ""),
]
GROQ_MODEL = "openai/gpt-oss-120b"

MISTRAL_API_KEYS = [
    os.environ.get("MISTRAL_API_KEY_1", ""),
    os.environ.get("MISTRAL_API_KEY_2", ""),
    os.environ.get("MISTRAL_API_KEY_3", ""),
]
MISTRAL_MODEL = "mistral-small-latest"  # free-tier model, no card required

GEMINI_API_KEYS = [
    os.environ.get("GEMINI_API_KEY_1", ""),
    os.environ.get("GEMINI_API_KEY_2", ""),
    os.environ.get("GEMINI_API_KEY_3", ""),
]
GEMINI_MODEL = "gemini-flash-latest"  # alias Google keeps pointed at the current GA flash model

GEMINI_MODEL = "gemini-flash-latest"
 
MAX_RETRIES = 3
BASE_DELAY = 2
FULL_EXHAUST_SLEEP = 24 * 60 * 60
NETWORK_RETRY_DELAY_CAP = 5 * 60
CHECKPOINT_FILE = "progress_checkpoint.json"
 
# How many videos to have "in flight" for the YouTube-fetch step at once.
# Keep this LOW (2-4) since YouTube blocks by IP, not by key — going too high
# here is what gets you 24-48h blocked, unlike the LLM-conversion step below.
YT_FETCH_CONCURRENCY = 3
YT_FETCH_DELAY_MIN = 2.0
YT_FETCH_DELAY_MAX = 4.5
 
WEBSHARE_PROXY_USERNAME = os.environ.get("WEBSHARE_PROXY_USERNAME", "")
WEBSHARE_PROXY_PASSWORD = os.environ.get("WEBSHARE_PROXY_PASSWORD", "")
 
TRANSCRIPT_LANGUAGES = ['hi', 'en', 'en-IN']
 
LATIN_FONT_NAME = "Helvetica"
 
_DEVANAGARI_FONT_CANDIDATES = [
    r"C:\Users\mishr\Downloads\NotoSansDevanagari-Regular.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf",
    r"C:\Windows\Fonts\Mangal.ttf",
    r"C:\Windows\Fonts\NotoSansDevanagari-Regular.ttf",
    "C:/Users/mishr/Downloads/NotoSansDevanagari-Regular (1).ttf",
]
 
DEVANAGARI_FONT_NAME = "Helvetica"
for _font_path in _DEVANAGARI_FONT_CANDIDATES:
    if os.path.exists(_font_path):
        try:
            pdfmetrics.registerFont(TTFont("DevanagariBody", _font_path))
            DEVANAGARI_FONT_NAME = "DevanagariBody"
            break
        except Exception:
            continue
 
if DEVANAGARI_FONT_NAME == "Helvetica":
    print("WARNING: No static Devanagari-capable font found. Leftover Hindi text "
          "will render as black boxes in the PDF.")
 
_DEVANAGARI_RANGE = re.compile(r'[\u0900-\u097F]')
_DEVANAGARI_RUN = re.compile(r'[\u0900-\u097F]+')
 
 
def make_mixed_font_markup(text):
    escaped = (text.replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;'))
 
    def repl(m):
        return f'<font face="{DEVANAGARI_FONT_NAME}">{m.group(0)}</font>'
 
    return _DEVANAGARI_RUN.sub(repl, escaped)
 
 
GARBAGE_LETTER_N_RATIO = 0.25
GARBAGE_LONG_RUN_RATIO = 0.15
 
 
# ---- Provider call exceptions ----
 
class RateLimited(Exception):
    pass
 
class TransientError(Exception):
    pass
 
class NetworkError(Exception):
    pass
 
class ConfigError(Exception):
    pass
 
 
_NETWORK_EXCEPTIONS = (ConnectionError, TimeoutError, OSError, socket.gaierror)
_CONFIG_ERROR_MARKERS = ("does not exist", "no longer available", "not found", "model_not_found",
                          "payment required", "insufficient credit", "quota exceeded for this billing")
 
 
def _is_config_error(message):
    lowered = message.lower()
    return any(marker in lowered for marker in _CONFIG_ERROR_MARKERS)
 
 
def make_groq_caller(api_key, model):
    client = Groq(api_key=api_key)
 
    def call(prompt):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except GroqRateLimitError:
            raise RateLimited()
        except GroqAPIConnectionError as e:
            raise NetworkError(str(e))
        except GroqAPIError as e:
            if _is_config_error(str(e)):
                raise ConfigError(str(e))
            raise TransientError(str(e))
        except _NETWORK_EXCEPTIONS as e:
            raise NetworkError(str(e))
 
    return call
 
 
def make_mistral_caller(api_key, model):
    client = OpenAI(api_key=api_key, base_url="https://api.mistral.ai/v1")
 
    def call(prompt):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except OAIRateLimitError:
            raise RateLimited()
        except OAIAPIConnectionError as e:
            raise NetworkError(str(e))
        except OAIAPIError as e:
            if _is_config_error(str(e)):
                raise ConfigError(str(e))
            raise TransientError(str(e))
        except _NETWORK_EXCEPTIONS as e:
            raise NetworkError(str(e))
 
    return call
 
 
def make_gemini_caller(api_key, model):
    genai.configure(api_key=api_key)
    gmodel = genai.GenerativeModel(model)
 
    def call(prompt):
        try:
            response = gmodel.generate_content(prompt)
            return response.text.strip()
        except GeminiRateLimitError:
            raise RateLimited()
        except (GeminiServiceUnavailable, GeminiDeadlineExceeded) as e:
            raise NetworkError(str(e))
        except GeminiAPIError as e:
            if _is_config_error(str(e)):
                raise ConfigError(str(e))
            raise TransientError(str(e))
        except _NETWORK_EXCEPTIONS as e:
            raise NetworkError(str(e))
 
    return call
 
 
providers = []
 
for i, key in enumerate(GROQ_API_KEYS, 1):
    if key:
        providers.append((f"groq-key-{i}", make_groq_caller(key, GROQ_MODEL)))
 
for i, key in enumerate(MISTRAL_API_KEYS, 1):
    if key:
        providers.append((f"mistral-key-{i}", make_mistral_caller(key, MISTRAL_MODEL)))
 
for i, key in enumerate(GEMINI_API_KEYS, 1):
    if key:
        providers.append((f"gemini-key-{i}", make_gemini_caller(key, GEMINI_MODEL)))
 
if not providers:
    raise RuntimeError(
        "No providers configured. Set the GROQ_API_KEY_*, MISTRAL_API_KEY_*, "
        "and/or GEMINI_API_KEY_* environment variables."
    )
 
NUM_PROVIDERS = len(providers)
 
# --- Per-provider rate limiting -------------------------------------------
# Free-tier APIs cap requests per MINUTE, often quite low (Gemini/Mistral free
# tiers especially). With 9 workers cycling through providers, back-to-back
# reuse of the same key across consecutive videos can blow through that cap in
# seconds even though only one worker touches a given key at a time. This
# enforces a minimum gap between calls to the SAME provider, tuned per family.
# Lower these if your account has a higher paid-tier limit; raise them if you
# still see rate limiting.
PROVIDER_MIN_INTERVAL = {
    "groq": 2.0,
    "mistral": 6.0,
    "gemini": 4.0,
}
 
_provider_last_call = [0.0] * NUM_PROVIDERS
_provider_rate_lock = threading.Lock()
 
 
def _provider_family(name):
    return name.split("-")[0]
 
 
def _throttle_provider(idx):
    name, _ = providers[idx]
    min_interval = PROVIDER_MIN_INTERVAL.get(_provider_family(name), 3.0)
    with _provider_rate_lock:
        now = time.time()
        wait = _provider_last_call[idx] + min_interval - now
        if wait > 0:
            time.sleep(wait)
        _provider_last_call[idx] = time.time()
 
# --- Thread-safe "next free provider" pool -------------------------------
# Instead of one shared round-robin index (which serializes everyone onto
# whichever provider is "current"), each worker thread checks a provider out
# of this pool for the whole video it's processing, and returns it when done.
# Uses a Queue (blocking, no busy-wait) so checkout_provider() correctly
# blocks the caller until a provider is checked back in, without holding any
# lock while it waits.
import queue as _queue_module
 
_provider_pool = _queue_module.Queue()
for _i in range(NUM_PROVIDERS):
    _provider_pool.put(_i)
 
 
def checkout_provider():
    return _provider_pool.get()  # blocks until one is available
 
 
def checkin_provider(idx):
    _provider_pool.put(idx)
 
 
def convert_with_provider(provider_idx, prompt):
    """Runs the prompt through ONE specific provider (with its own retry/backoff),
    falling back to any other provider only if this one is fully exhausted."""
    tried = set()
    idx = provider_idx
    network_wait = 5
 
    while True:
        if idx in tried and len(tried) >= NUM_PROVIDERS:
            print(f"    -> All {NUM_PROVIDERS} providers rate-limited/exhausted for this "
                  f"chunk. Sleeping 24h...")
            time.sleep(FULL_EXHAUST_SLEEP)
            tried = set()
 
        name, call = providers[idx]
        got_network_error = False
 
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                _throttle_provider(idx)
                result = call(prompt)
                return result
            except RateLimited:
                cooldown = random.uniform(15, 30)
                print(f"    -> [{name}] rate limited. Cooling down {cooldown:.0f}s "
                      f"before trying the next provider...")
                time.sleep(cooldown)
                break
            except ConfigError as e:
                print(f"    -> [{name}] bad model/config, skipping ({str(e)[:100]}).")
                break
            except NetworkError as e:
                print(f"    -> [{name}] network problem ({str(e)[:80]}). "
                      f"Waiting {network_wait}s...")
                time.sleep(network_wait)
                network_wait = min(network_wait * 2, NETWORK_RETRY_DELAY_CAP)
                got_network_error = True
                continue
            except TransientError as e:
                wait = BASE_DELAY * (2 ** (attempt - 1))
                print(f"    -> [{name}] transient error ({str(e)[:80]}). "
                      f"Retry {attempt}/{MAX_RETRIES} in {wait}s...")
                time.sleep(wait)
 
        if got_network_error:
            continue  # keep hammering the same provider on pure network issues
 
        tried.add(idx)
        idx = (idx + 1) % NUM_PROVIDERS
 
 
def chunk_text(text, max_chars=12000):
    # Bumped from 6000 -> 12000: fewer, larger chunks means fewer round-trips
    # per video. gpt-oss-120b / mistral-small / gemini-flash all comfortably
    # handle this much context; lower it back down if you see truncated output.
    words = text.split()
    chunks, current = [], []
    length = 0
    for w in words:
        length += len(w) + 1
        if length > max_chars:
            chunks.append(" ".join(current))
            current, length = [w], len(w) + 1
        else:
            current.append(w)
    if current:
        chunks.append(" ".join(current))
    return chunks
 
 
def extract_video_id(url):
    pattern = r'(?:v=|\/v\/|embed\/|youtu\.be\/|\/shorts\/|^)([A-Za-z0-9_-]{11})'
    match = re.search(pattern, url)
    return match.group(1) if match else None
 
 
def convert_to_hinglish(devanagari_text, provider_idx, strict=False):
    strict_note = (
        "\n\nIMPORTANT: Your output must contain ZERO Devanagari characters. "
        "Every single word must be in Roman/Latin script. If you are unsure how to "
        "spell a Hindi word in Roman script, use your best phonetic transliteration -- "
        "never leave any word in Devanagari."
        if strict else ""
    )
    prompt = f"""The following text is a YouTube auto-generated transcript written in 
Devanagari script, but the speaker was actually speaking Hinglish (mixed Hindi-English) 
or English. Convert it into natural, properly spelled Roman script (English where the 
speaker used English words, and Romanized Hindi where they used Hindi words). 
Do not translate meaning — just convert the script to how it would naturally be typed. 
Do not add commentary, headers, or notes. Output only the converted text.{strict_note}
 
Text:
{devanagari_text}"""
    return convert_with_provider(provider_idx, prompt)
 
 
def fetch_best_transcript(ytt_api, video_id, languages=None):
    languages = languages or TRANSCRIPT_LANGUAGES
    try:
        transcript_list = ytt_api.list(video_id)
    except AttributeError:
        return ytt_api.fetch(video_id, languages=languages), True
 
    try:
        manual = transcript_list.find_manually_created_transcript(languages)
        return manual.fetch(), False
    except Exception:
        pass
 
    generated = transcript_list.find_generated_transcript(languages)
    return generated.fetch(), True
 
 
def is_garbage_transcript(text):
    if not text.strip():
        return True
    letters = [c for c in text.lower() if c.isalpha()]
    if not letters:
        return True
    n_ratio = letters.count('n') / len(letters)
    long_runs = re.findall(r'n{4,}', text.lower())
    long_run_chars = sum(len(r) for r in long_runs)
    long_run_ratio = long_run_chars / max(len(text), 1)
    return n_ratio > GARBAGE_LETTER_N_RATIO or long_run_ratio > GARBAGE_LONG_RUN_RATIO
 
 
# ---- Checkpointing (thread-safe, batched flush) ----
 
# Flush to disk every N completed videos instead of after every single one —
# with 9 workers finishing close together, writing the whole JSON file on
# every completion is wasted I/O. The in-memory dict is still updated
# immediately, so nothing is lost logically; only the disk write is batched.
CHECKPOINT_FLUSH_EVERY = 9
 
_checkpoint_lock = threading.Lock()
_pending_since_flush = 0
 
 
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}
 
 
def _flush_checkpoint_to_disk(checkpoint):
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHECKPOINT_FILE)
 
 
def save_checkpoint_entry(checkpoint, key, entry, force_flush=False):
    """Thread-safe: updates one entry in memory always; flushes to disk every
    CHECKPOINT_FLUSH_EVERY entries, or immediately if force_flush=True (e.g.
    after an IP block, or the last video in the batch)."""
    global _pending_since_flush
    with _checkpoint_lock:
        checkpoint[key] = entry
        _pending_since_flush += 1
        if force_flush or _pending_since_flush >= CHECKPOINT_FLUSH_EVERY:
            _flush_checkpoint_to_disk(checkpoint)
            _pending_since_flush = 0
 
 
def flush_checkpoint_now(checkpoint):
    """Force an immediate disk flush regardless of the batch counter."""
    global _pending_since_flush
    with _checkpoint_lock:
        _flush_checkpoint_to_disk(checkpoint)
        _pending_since_flush = 0
 
 
# Bounds concurrent YouTube fetches (IP-ban risk), separate from the LLM
# conversion concurrency (key-ban risk, one key per worker so no shared limit).
_yt_fetch_semaphore = threading.Semaphore(YT_FETCH_CONCURRENCY)
_ip_blocked_event = threading.Event()
 
 
def process_one_video(ytt_api, record, index, total, checkpoint, provider_idx):
    url = record['url']
    date = record.get('published_date', record.get('date', ''))
    video_id = extract_video_id(url)
    key = video_id or f"invalid-row-{index}"
 
    if key in checkpoint:
        return f"[{index}/{total}] Already done, skipping: {key}"
 
    if not video_id:
        msg = f"[{index}/{total}] Skipped: Invalid URL format -> {url}"
        save_checkpoint_entry(checkpoint, key,
                               {"index": index, "url": url, "date": date,
                                "status": "invalid_url", "text": msg})
        return msg
 
    if _ip_blocked_event.is_set():
        return f"[{index}/{total}] Skipping (IP block detected): {video_id}"
 
    try:
        with _yt_fetch_semaphore:
            time.sleep(random.uniform(YT_FETCH_DELAY_MIN, YT_FETCH_DELAY_MAX))
            raw_transcript, was_auto = fetch_best_transcript(ytt_api, video_id)
 
        full_text = " ".join([snippet.text for snippet in raw_transcript])
 
        if was_auto and is_garbage_transcript(full_text):
            save_checkpoint_entry(checkpoint, key,
                                   {"index": index, "url": url, "date": date,
                                    "status": "raw_fallback", "text": full_text})
            return f"[{index}/{total}] Raw fallback (garbage captions): {video_id}"
 
        chunks = chunk_text(full_text)
        converted_parts = []
        for i, chunk in enumerate(chunks, 1):
            converted = convert_to_hinglish(chunk, provider_idx)
            if _DEVANAGARI_RANGE.search(converted):
                converted = convert_to_hinglish(chunk, provider_idx, strict=True)
            converted_parts.append(converted)
        converted_text = " ".join(converted_parts)
 
        save_checkpoint_entry(checkpoint, key,
                               {"index": index, "url": url, "date": date,
                                "status": "ok", "text": converted_text})
        return f"[{index}/{total}] Done: {video_id}"
 
    except Exception as e:
        is_ip_block = False
        if RequestBlocked is not None and isinstance(e, (RequestBlocked, IpBlocked)):
            is_ip_block = True
        elif "blocking requests from your ip" in str(e).lower():
            is_ip_block = True
 
        if is_ip_block:
            _ip_blocked_event.set()
            flush_checkpoint_now(checkpoint)  # don't wait for the batch to fill on a block
            return (f"[{index}/{total}] *** YOUTUBE IP BLOCK hit at {video_id}. *** "
                    f"No further fetches will be attempted this run. Set WEBSHARE_PROXY_* "
                    f"env vars and rerun — checkpoint will resume from here.")
 
        save_checkpoint_entry(checkpoint, key,
                               {"index": index, "url": url, "date": date,
                                "status": "error", "text": str(e)})
        return f"[{index}/{total}] Failed: {str(e)[:120]}"
 
 
def batch_process_transcripts(record_list, output_file="all_transcripts1.pdf"):
    checkpoint = load_checkpoint()
    print(f"Loaded checkpoint with {len(checkpoint)} previously processed video(s).")
 
    if WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD and WebshareProxyConfig is not None:
        ytt_api = YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=WEBSHARE_PROXY_USERNAME,
                proxy_password=WEBSHARE_PROXY_PASSWORD,
            )
        )
        print("Using Webshare proxy for transcript fetches.")
    else:
        ytt_api = YouTubeTranscriptApi()
 
    total = len(record_list)
    print(f"Starting extraction for {total} videos using {NUM_PROVIDERS} parallel "
          f"provider workers (YT fetch concurrency capped at {YT_FETCH_CONCURRENCY})...")
 
    with ThreadPoolExecutor(max_workers=NUM_PROVIDERS) as executor:
        futures = []
        for index, record in enumerate(record_list, 1):
            provider_idx = checkout_provider()
 
            def task(rec=record, idx=index, p_idx=provider_idx):
                try:
                    return process_one_video(ytt_api, rec, idx, total, checkpoint, p_idx)
                finally:
                    checkin_provider(p_idx)
 
            futures.append(executor.submit(task))
 
        for future in as_completed(futures):
            print(future.result())
            if _ip_blocked_event.is_set():
                break
 
    flush_checkpoint_now(checkpoint)  # make sure the last partial batch is on disk
    build_pdf_from_checkpoint(checkpoint, output_file)
 
 
def build_pdf_from_checkpoint(checkpoint, output_file="all_transcripts1.pdf"):
    if os.path.exists(output_file):
        os.remove(output_file)
 
    doc = SimpleDocTemplate(
        output_file,
        pagesize=A4,
        leftMargin=0.75*inch,
        rightMargin=0.75*inch,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch
    )
 
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('VideoTitle', parent=styles['Heading2'], fontSize=13, spaceAfter=6, textColor='#1a1a1a', fontName=LATIN_FONT_NAME)
    meta_style = ParagraphStyle('Meta', parent=styles['Normal'], fontSize=9, textColor='#555555', spaceAfter=10, fontName=LATIN_FONT_NAME)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=10.5, leading=16, alignment=TA_LEFT, spaceAfter=6, fontName=LATIN_FONT_NAME)
    error_style = ParagraphStyle('Error', parent=styles['Normal'], fontSize=10, textColor='#b00020', fontName=LATIN_FONT_NAME)
    warn_style = ParagraphStyle('Warn', parent=styles['Normal'], fontSize=10, textColor='#a06000', fontName=LATIN_FONT_NAME)
 
    story = []
    entries = sorted(checkpoint.values(), key=lambda e: e["index"])
 
    for entry in entries:
        index = entry["index"]
        url = entry.get("url", "")
        date = entry.get("date", "N/A")
        status = entry["status"]
 
        if status == "ok":
            safe_text = make_mixed_font_markup(entry["text"])
            story.append(Paragraph(f"Video {index}", title_style))
            story.append(Paragraph(f"URL: {url}", meta_style))
            story.append(Paragraph(f"Date: {date}", meta_style))
            story.append(Paragraph(safe_text, body_style))
        elif status == "raw_fallback":
            safe_text = make_mixed_font_markup(entry["text"])
            story.append(Paragraph(f"Video {index} (raw, unconverted)", title_style))
            story.append(Paragraph(f"URL: {url}", meta_style))
            story.append(Paragraph(f"Date: {date}", meta_style))
            story.append(Paragraph(
                "YouTube's auto-captions for this video looked like filler garbage, so "
                "Hinglish conversion was skipped. This is the original raw transcript text:",
                warn_style))
            story.append(Paragraph(safe_text, body_style))
        else:
            safe_err = make_mixed_font_markup(entry["text"])
            story.append(Paragraph(f"Video {index}: Error", title_style))
            story.append(Paragraph(f"URL: {url}", meta_style))
            story.append(Paragraph(f"Date: {date}", meta_style))
            story.append(Paragraph(f"ERROR: {safe_err}", error_style))
 
        story.append(PageBreak())
 
    if story and isinstance(story[-1], PageBreak):
        story.pop()
 
    doc.build(story)
 
    ok_count = sum(1 for e in entries if e["status"] == "ok")
    raw_count = sum(1 for e in entries if e["status"] == "raw_fallback")
    error_count = sum(1 for e in entries if e["status"] not in ("ok", "raw_fallback"))
    print(f"\nProcessing complete! {ok_count} converted to Hinglish, {raw_count} saved as "
          f"raw script (garbage captions), {error_count} errored. Saved into: {os.path.abspath(output_file)}")
 
 
if __name__ == "__main__":
    batch_process_transcripts(records)