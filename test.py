import os
from googleapiclient.discovery import build

API_KEY = "AIzaSyDnsZsieodbA89wEj3X_mjxyg4BVfTAkuY"
youtube = build('youtube', 'v3', developerKey=API_KEY)

def get_historical_mandi_videos():
    all_videos = []
    next_page_token = None
    
    # Define the 5-year window (2021 to 2026)
    start_date = "2021-01-01T00:00:00Z"
    end_date = "2026-01-01T00:00:00Z"
    
    while True:
        request = youtube.search().list(
            q="chhattisgarh mandi bhav",
            part="id,snippet",
            type="video",
            publishedAfter=start_date,
            publishedBefore=end_date,
            order="date",
            maxResults=50, # Max allowed per request
            pageToken=next_page_token
        )
        response = request.execute()
        
        for item in response.get("items", []):
            video_id = item["id"]["videoId"]
            all_videos.append({
                "videoId": video_id,
                "title": item["snippet"]["title"],
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "publishedAt": item["snippet"]["publishedAt"]
            })
            
        # Check if there's another page of results
        next_page_token = response.get("nextPageToken")
        if not next_page_token or len(all_videos) >= 5000: # Optional cap for testing
            break
            
    return all_videos

# Run the historical fetch
historical_vids = get_historical_mandi_videos()
print(f"Total historical videos found: {len(historical_vids)}")
# print(historical_vids)

import pandas as pd

df = pd.DataFrame(historical_vids)
df = df[["videoId", "title", "url", "publishedAt"]]
df.to_csv("chhattisgarh_mandi_videos.csv", index=False, encoding="utf-8")

print(df.head)