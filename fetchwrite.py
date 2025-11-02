import requests
import sqlite3
from tqdm import tqdm
import re
import time

DB_FILE = "writing_quiz.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/115.0 Safari/537.36"
}

# テーブル作成
def create_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS writing_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_text TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    print("✅ テーブル確認・作成完了")

# ランダムページから文章取得
def fetch_japanese_sentences(total=1000):
    sentences = []
    attempts = 0
    print(f"🌐 Wikipediaから文章を取得中 ({total}件目標)...")
    while len(sentences) < total and attempts < total*5:
        attempts += 1
        try:
            r = requests.get("https://ja.wikipedia.org/api/rest_v1/page/random/summary",
                             headers=HEADERS, timeout=5)
            if r.status_code != 200:
                continue
            data = r.json()
            text = data.get("extract", "")
            if text:
                for s in re.split("。|\n", text):
                    s = s.strip()
                    if len(s) > 10:
                        sentences.append(s)
        except Exception:
            continue
        if attempts % 10 == 0:
            print(f"  試行回数: {attempts}, 取得済み: {len(sentences)} 件")
        time.sleep(0.1)
    print(f"✅ 文章取得完了: {len(sentences[:total])} 件")
    return sentences[:total]

# DB に登録
def insert_prompts(japanese_sentences):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    print("💾 DBに登録中...")
    for sentence in tqdm(japanese_sentences, desc="登録中"):
        c.execute("INSERT INTO writing_prompts (prompt_text) VALUES (?)", (sentence,))
    conn.commit()
    conn.close()
    print(f"✅ {len(japanese_sentences)} 件の問題をDBに登録しました。")

if __name__ == "__main__":
    create_table()
    jp_sentences = fetch_japanese_sentences(1000)
    insert_prompts(jp_sentences)
