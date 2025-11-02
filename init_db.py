# init_db.py
import sqlite3

# ===== 単語クイズ用 DB =====
DB_FILE = "english_learning.db"
conn = sqlite3.connect(DB_FILE)
c = conn.cursor()

# users テーブル
c.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    password TEXT
)
''')

# words テーブル
c.execute('''
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT UNIQUE,
    definition_ja TEXT
)
''')

# 学習履歴テーブル
c.execute('''
CREATE TABLE IF NOT EXISTS student_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    word_id INTEGER,
    score INTEGER,
    feedback TEXT,
    example TEXT,
    attempt_date TEXT,
    is_wrong INTEGER DEFAULT 0,
    wrong_count INTEGER DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(word_id) REFERENCES words(id)
)
''')

# 初期単語データ
words = [
    ("apple", "りんご"),
    ("banana", "バナナ"),
    ("orange", "オレンジ"),
    ("grape", "ぶどう"),
    ("peach", "もも")
]

# 既存データを消さずに追加
for w, d in words:
    c.execute("INSERT OR IGNORE INTO words (word, definition_ja) VALUES (?, ?)", (w, d))

conn.commit()
conn.close()

# ===== 英作文用 DB =====
WRITING_DB = "writing_quiz.db"
conn = sqlite3.connect(WRITING_DB)
c = conn.cursor()

# お題テーブル
c.execute('''
CREATE TABLE IF NOT EXISTS writing_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_text TEXT
)
''')

# 回答テーブル
c.execute('''
CREATE TABLE IF NOT EXISTS writing_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    prompt_id INTEGER,
    answer TEXT,
    score INTEGER,
    feedback TEXT,
    correct_example TEXT,
    attempt_date TEXT,
    is_wrong INTEGER DEFAULT 0,
    wrong_count INTEGER DEFAULT 0
)
''')

# 初期お題データ
prompts = [
    "私は昨日、映画を見ました。",
    "明日は雨が降るでしょう。",
    "昨日の夜ご飯は何を食べましたか？",
    "私は英語を勉強しています。",
    "週末に友達と遊びます。"
]

for p in prompts:
    c.execute("INSERT OR IGNORE INTO writing_prompts (prompt_text) VALUES (?)", (p,))

conn.commit()
conn.close()

print("✅ 初期データベースが作成されました！")
