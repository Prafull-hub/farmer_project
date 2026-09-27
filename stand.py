import os
import re
import json
import pandas as pd
 
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
 
# ---- Same paths as the main script ----
CSV_PATH = "E:/Python/farmerproject/chhattisgarh_.mandi.csv"
CHECKPOINT_FILE = "progress_checkpoint.json"
OUTPUT_PDF = "all_transcripts1.pdf"
 
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
 
_DEVANAGARI_RUN = re.compile(r'[\u0900-\u097F]+')
 
 
def make_mixed_font_markup(text):
    escaped = (str(text).replace('&', '&amp;')
                         .replace('<', '&lt;')
                         .replace('>', '&gt;'))
 
    def repl(m):
        return f'<font face="{DEVANAGARI_FONT_NAME}">{m.group(0)}</font>'
 
    return _DEVANAGARI_RUN.sub(repl, escaped)
 
 
def extract_video_id(url):
    pattern = r'(?:v=|\/v\/|embed\/|youtu\.be\/|\/shorts\/|^)([A-Za-z0-9_-]{11})'
    match = re.search(pattern, str(url))
    return match.group(1) if match else None
 
 
def build_pdf_from_checkpoint(checkpoint, output_file=OUTPUT_PDF):
    if os.path.exists(output_file):
        os.remove(output_file)
 
    doc = SimpleDocTemplate(
        output_file, pagesize=A4,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch
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
        date = entry.get("date", "N/A") or "N/A"
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
    print(f"Rebuilt PDF -> {os.path.abspath(output_file)}")
 
 
def main():
    print("Loading CSV for date lookup...")
    df = pd.read_csv(CSV_PATH)
 
    # Build video_id -> published_date map from the CSV
    date_by_video_id = {}
    for _, row in df.iterrows():
        vid = extract_video_id(row['url'])
        if vid:
            date_by_video_id[vid] = row['published_date']
 
    print(f"Loaded {len(date_by_video_id)} url->date mappings from CSV.")
 
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        checkpoint = json.load(f)
 
    updated = 0
    still_missing = 0
    for key, entry in checkpoint.items():
        current_date = entry.get("date", "")
        # Treat '', None, NaN, and the string 'nan' all as "missing"
        is_missing = (
            current_date is None
            or current_date == ""
            or (isinstance(current_date, float) and pd.isna(current_date))
            or str(current_date).lower() == "nan"
        )
        if not is_missing:
            continue
 
        vid = extract_video_id(entry.get("url", "")) or key
        new_date = date_by_video_id.get(vid)
        if new_date is not None and not (isinstance(new_date, float) and pd.isna(new_date)):
            entry["date"] = new_date
            updated += 1
        else:
            still_missing += 1
 
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHECKPOINT_FILE)
 
    print(f"Backfilled {updated} date(s). {still_missing} entries still have no matching "
          f"date in the CSV (URL/video ID couldn't be matched, or CSV date itself is blank).")
 
    build_pdf_from_checkpoint(checkpoint, OUTPUT_PDF)
 
 
if __name__ == "__main__":
    main()