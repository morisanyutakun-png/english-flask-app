import sqlite3

DB_FILE = "writing_quiz.db"

def create_tables():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # お題テーブル（すでにある場合はスキップ）
    c.execute("""
        CREATE TABLE IF NOT EXISTS writing_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_text TEXT NOT NULL
        )
    """)

    # 回答テーブル（これが今回必要！）
    c.execute("""
        CREATE TABLE IF NOT EXISTS writing_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            prompt_id INTEGER,
            answer_text TEXT,
            score INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("✅ writing_prompts / writing_answers テーブルを確認・作成しました。")

if __name__ == "__main__":
    create_tables()
