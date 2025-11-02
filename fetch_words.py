import sqlite3
import requests
import time
from googletrans import Translator  # pip install googletrans==4.0.0rc1

DB_FILE = "english_learning.db"
WORDLIST_FILE = "words_alpha.txt"
API_DELAY = 0.5
BATCH_SIZE = 50

translator = Translator()

# --- DB作成 ---
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT UNIQUE,
    definition_en TEXT,
    definition_ja TEXT
)
""")
conn.commit()

cur.execute("SELECT word FROM words")
existing_words = set(row[0] for row in cur.fetchall())
conn.close()

# --- 単語リスト ---
with open(WORDLIST_FILE, "r") as f:
    words = [w.strip() for w in f if w.strip() and w.strip() not in existing_words]

print(f"処理する単語数: {len(words)}")

batch = []
try:
    for word in words:
        # API取得
        try:
            r = requests.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=5)
            time.sleep(API_DELAY)
            if r.status_code != 200:
                print(f"[Skipped] {word} は意味取得できず")
                continue
            data = r.json()
            definition_en = data[0]["meanings"][0]["definitions"][0]["definition"]
            # 日本語に翻訳
            definition_ja = translator.translate(definition_en, src='en', dest='ja').text
        except Exception as e:
            print(f"[Error] {word}: {e}")
            continue

        batch.append((word, definition_en, definition_ja))
        print(f"{word} を取得しました")

        if len(batch) >= BATCH_SIZE:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.executemany(
                "INSERT OR IGNORE INTO words (word, definition_en, definition_ja) VALUES (?, ?, ?)", 
                batch
            )
            conn.commit()
            conn.close()
            print(f"{len(batch)}件をDBに書き込みました")
            batch = []

except KeyboardInterrupt:
    print("\n処理を中断しました。ここまで取得した単語はDBに保存済みです。")

if batch:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR IGNORE INTO words (word, definition_en, definition_ja) VALUES (?, ?, ?)", 
        batch
    )
    conn.commit()
    conn.close()
    print(f"最後の{len(batch)}件をDBに書き込みました")

print("完了！")
