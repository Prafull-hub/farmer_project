# """
# Chhattisgarh Mandi YouTube Video Finder
# ----------------------------------------
# Searches YouTube for mandi/sabji-rate videos (Raipur, Durg, etc.) over the
# last N years and exports title, published date, channel, and URL to CSV.

# SETUP (one-time):
# 1. Go to https://console.cloud.google.com/
# 2. Create a project -> Enable "YouTube Data API v3"
# 3. Create credentials -> API key -> paste it below in API_KEY
# 4. pip install google-api-python-client --break-system-packages

# QUOTA NOTE:
# Each search.list call costs 100 quota units. Free tier = 10,000 units/day
# = 100 search calls/day. Each query below can use multiple calls (pagination),
# so don't add too many queries in one run - split across days if needed.
# """

# import csv
# import time
# from datetime import datetime, timedelta, timezone
# from googleapiclient.discovery import build
# from googleapiclient.errors import HttpError

# # ---------------- CONFIG ----------------
# API_KEY = "AIzaSyDLmFSic4vKGenOV33cAdUCnjXkGsWcGJM"

# # search terms — add/remove mandis as you like
# QUERIES = [
#     "raipur mandi sabji rate",
#     "durg mandi sabji rate",
#     "bhilai mandi sabji rate",
#     "rajnandgaon mandi bhav",
#     "bilaspur mandi sabji rate",
#     "chhattisgarh mandi bhav today",
#     "chhattisgarh mandi sabji rate",
# ]

# YEARS_BACK = 5          # change to 10 if you want 10 years
# MAX_RESULTS_PER_QUERY = 200   # roughly caps pagination (50 per page * 4 pages)
# OUTPUT_CSV = "chhattisgarhmandi_.csv"
# # -----------------------------------------

# def get_date_range(years_back):
#     now = datetime.now(timezone.utc)
#     start = now - timedelta(days=365 * years_back)
#     # RFC3339 format required by API
#     return start.isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")


# def search_videos(youtube, query, published_after, published_before, max_results):
#     results = []
#     next_page_token = None
#     fetched = 0

#     while fetched < max_results:
#         try:
#             request = youtube.search().list(
#                 part="snippet",
#                 q=query,
#                 type="video",
#                 order="date",              # newest first
#                 publishedAfter=published_after,
#                 publishedBefore=published_before,
#                 maxResults=50,
#                 pageToken=next_page_token,
#                 relevanceLanguage="hi",
#                 regionCode="IN",
#             )
#             response = request.execute()
#         except HttpError as e:
#             print(f"  API error for query '{query}': {e}")
#             break

#         for item in response.get("items", []):
#             vid = item["id"]["videoId"]
#             snippet = item["snippet"]
#             results.append({
#                 "title": snippet["title"],
#                 "published_date": snippet["publishedAt"][:10],  # YYYY-MM-DD
#                 "channel": snippet["channelTitle"],
#                 "url": f"https://www.youtube.com/watch?v={vid}",
#                 "matched_query": query,
#             })

#         fetched += len(response.get("items", []))
#         next_page_token = response.get("nextPageToken")
#         if not next_page_token:
#             break
#         time.sleep(0.3)  # be gentle

#     return results


# def main():
#     youtube = build("youtube", "v3", developerKey=API_KEY)
#     published_after, published_before = get_date_range(YEARS_BACK)

#     all_results = []
#     seen_ids = set()

#     for query in QUERIES:
#         print(f"Searching: {query}")
#         results = search_videos(youtube, query, published_after, published_before, MAX_RESULTS_PER_QUERY)
#         for r in results:
#             vid_key = r["url"]
#             if vid_key not in seen_ids:
#                 seen_ids.add(vid_key)
#                 all_results.append(r)
#         print(f"  -> {len(results)} results ({len(all_results)} unique so far)")

#     # sort newest first
#     all_results.sort(key=lambda r: r["published_date"], reverse=True)

#     with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
#         writer = csv.DictWriter(f, fieldnames=["title", "published_date", "channel", "url", "matched_query"])
#         writer.writeheader()
#         writer.writerows(all_results)

#     print(f"\nDone. {len(all_results)} unique videos saved to {OUTPUT_CSV}")


# if __name__ == "__main__":
#     main()


import pandas as pd

df = pd.read_csv('E:/Python/farmerproject/chhattisgarh_mandi_videos.csv')
df["publishedAt"] = pd.to_datetime(df["publishedAt"])
df["publishedAt"] = df["publishedAt"].dt.date
df.to_csv('E:/Python/farmerproject/chhattisgarh_mandi_videos.csv', index=False)
print(df.head())