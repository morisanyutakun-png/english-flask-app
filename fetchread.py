# fetch_reading_gutenberg.py
import requests
import sqlite3
import re

# ================================
# DB作成
# ================================
DB_FILE = "reading_quiz.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS reading_texts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            level TEXT,
            topic TEXT,
            source_url TEXT
        )
        """)
        conn.commit()
    print(f"DB initialized: {DB_FILE}")

# ================================
# 文を30語前後で分割
# ================================
def split_text(text, max_words=30):
    sentences = re.split(r'(?<=[.!?]) +', text.strip())
    chunks = []
    chunk = ""
    word_count = 0
    for sentence in sentences:
        words = sentence.split()
        if word_count + len(words) <= max_words:
            chunk += (" " if chunk else "") + sentence
            word_count += len(words)
        else:
            if chunk:
                chunks.append(chunk)
            chunk = sentence
            word_count = len(words)
    if chunk:
        chunks.append(chunk)
    return chunks

# ================================
# Project Gutenberg からテキストを取得
# ================================
def fetch_gutenberg_text(url):
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        text = resp.text
        # ヘッダ・フッタ除去（Gutenberg のライセンス部分）
        start = text.find("*** START OF")
        end = text.find("*** END OF")
        if start != -1 and end != -1:
            text = text[start:end]
        # 空行を1つのスペースに置換
        text = re.sub(r'\n+', ' ', text)
        return text.strip()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

# ================================
# サンプル短編英文URL（Project Gutenberg）
# ================================
GUTENBERG_URLS = [
    "https://www.gutenberg.org/files/11/11-0.txt",  # Alice's Adventures in Wonderland
    "https://www.gutenberg.org/files/1342/1342-0.txt"  # Pride and Prejudice
]

# ================================
# メイン処理
# ================================
def main():
    init_db()
    inserted_count = 0
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        for url in GUTENBERG_URLS:
            print(f"Fetching: {url}")
            text = fetch_gutenberg_text(url)
            if not text:
                continue
            chunks = split_text(text, max_words=30)
            for chunk in chunks:
                chunk = chunk.strip()
                if not chunk:
                    continue
                c.execute(
                    "INSERT INTO reading_texts (text, level, topic, source_url) VALUES (?, ?, ?, ?)",
                    (chunk, "初級〜中級", "高校レベル", url)
                )
                inserted_count += 1
        conn.commit()
    print(f"DB作成・データ格納完了！ {inserted_count} 件挿入されました。")

if __name__ == "__main__":
    main()
