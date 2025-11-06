# studyST/app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import datetime
import json
import re
import os
from flask_cors import CORS

# -----------------------
# Flask アプリ
# -----------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_secret_for_local_only")

# -----------------------
# CORS 設定
# -----------------------
CORS(app, origins=["https://english-flask-app.onrender.com"])

# -----------------------
# DB 設定
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DB_FILE = os.path.join(BASE_DIR, "english_learning.db")
REPO_WRITING_DB = os.path.join(BASE_DIR, "writing_quiz.db")

TMP_DIR = "/tmp"
DB_DIR = os.getenv("DB_DIR", TMP_DIR)
os.makedirs(DB_DIR, exist_ok=True)

DB_FILE = REPO_DB_FILE if os.path.exists(REPO_DB_FILE) else os.path.join(DB_DIR, "english_learning.db")
WRITING_DB = REPO_WRITING_DB if os.path.exists(REPO_WRITING_DB) else os.path.join(DB_DIR, "writing_quiz.db")

# -----------------------
# Gemini 設定
# -----------------------
HAS_GEMINI = False
try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        HAS_GEMINI = True
    else:
        print("⚠️ GEMINI_API_KEY not set; running without Gemini.")
except Exception as e:
    print("⚠️ google.generativeai not available or failed to init:", e)
    HAS_GEMINI = False

# -----------------------
# DB 初期化
# -----------------------
def init_db_file(path, create_statements):
    with sqlite3.connect(path) as conn:
        c = conn.cursor()
        for stmt in create_statements:
            c.execute(stmt)
        conn.commit()

def init_all_dbs():
    create_users_words = [
        '''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT UNIQUE,
            definition_ja TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS student_answers (
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
        )'''
    ]
    create_writing = [
        '''CREATE TABLE IF NOT EXISTS writing_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_text TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS writing_answers (
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
        )'''
    ]
    init_db_file(DB_FILE, create_users_words)
    init_db_file(WRITING_DB, create_writing)
    print("✅ DBs initialized:", DB_FILE, WRITING_DB)

init_all_dbs()

# -----------------------
# JSON 抽出ユーティリティ
# -----------------------
def parse_json_from_text(text):
    match = re.search(r'(\{(?:[^{}]|(?R))*\})', text, re.DOTALL)
    snippet = match.group(1) if match else None
    if not snippet:
        return None
    try:
        return json.loads(snippet)
    except Exception as e:
        print("JSON parse error:", e)
        return None

# -----------------------
# 採点関数
# -----------------------
def evaluate_answer(word, correct_meaning, user_answer):
    if not HAS_GEMINI:
        score = 100 if user_answer.strip() and correct_meaning in user_answer else 60
        feedback = "（簡易採点）" + ("Good!" if score >= 70 else "もう少し詳しく書いてみよう")
        example = f"Example: {word} is used like ... "
        return score, feedback, example, "", correct_meaning

    prompt = f"""
あなたは英語教師です。
単語: {word}
正しい意味（日本語）: {correct_meaning}
学習者の回答（日本語）: {user_answer}

JSON形式で出力:
{{"score":0,"feedback":"...","example":"...","pos":"...","simple_meaning":"..."}}
"""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        res = model.generate_content(prompt)
        data = parse_json_from_text(res.text or "")
        if data:
            return int(data.get("score",0)), data.get("feedback",""), data.get("example",""), data.get("pos",""), data.get("simple_meaning","")
    except Exception as e:
        print("Gemini Error:", e)
    return 0, "採点できませんでした。", "", "", ""

def evaluate_writing(prompt_text, answer):
    if not HAS_GEMINI:
        score = 80 if len(answer.split()) > 3 else 30
        return score, "（簡易採点）改善点を確認してください", "This is an example."
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        res = model.generate_content(f"お題:{prompt_text}\n回答:{answer}\nJSONで返して")
        data = parse_json_from_text(res.text or "")
        if data:
            return int(data.get("score",0)), data.get("feedback",""), data.get("correct_example","")
    except Exception as e:
        print("Gemini writing error:", e)
    return 0, "採点中にエラーが発生しました。", ""

# -----------------------
# DB 操作関数
# -----------------------
def get_random_word():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id, word, definition_ja FROM words ORDER BY RANDOM() LIMIT 1")
            return c.fetchone()
    except Exception as e:
        print("DB Error get_random_word:", e)
        return None

def get_average_score(user_id):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT AVG(score) FROM student_answers WHERE user_id=?", (user_id,))
            r = c.fetchone()
            avg = r[0] if r else None
            return round(avg,2) if avg else 0
    except Exception as e:
        print("DB Error get_average_score:", e)
        return 0

def get_random_prompt():
    try:
        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT id, prompt_text FROM writing_prompts ORDER BY RANDOM() LIMIT 1")
            row = c.fetchone()
            if row:
                return {"id":row[0], "text":row[1]}
    except Exception as e:
        print("DB Error get_random_prompt:", e)
    return {"id": None, "text": "お題が見つかりませんでした"}

# -----------------------
# API ルーティング（フロント向け）
# -----------------------
@app.route("/api/submit_answer", methods=["POST"])
def api_submit_answer():
    try:
        user_id = session.get("user_id", 0)
        word_id = request.form.get("word_id")
        answer = request.form.get("answer", "")

        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT word, definition_ja FROM words WHERE id=?", (word_id,))
            row = c.fetchone()
            if not row:
                return jsonify({"error":"単語が見つかりません"}), 404
            word, correct_meaning = row

        score, feedback, example, pos, simple_meaning = evaluate_answer(word, correct_meaning, answer)

        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO student_answers (user_id, word_id, score, feedback, example, attempt_date)
                VALUES (?,?,?,?,?,?)
            """, (user_id, word_id, score, feedback, example, datetime.datetime.now().isoformat()))
            conn.commit()

        return jsonify({
            "score": score,
            "feedback": feedback,
            "example": example,
            "pos": pos,
            "simple_meaning": simple_meaning,
        })
    except Exception as e:
        print("api_submit_answer error:", e)
        return jsonify({"error": "internal server error"}), 500

@app.route("/api/submit_writing", methods=["POST"])
def api_submit_writing():
    try:
        user_id = request.form.get("user_id", 0)
        prompt_id = request.form.get("prompt_id")
        answer = request.form.get("answer", "")

        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT prompt_text FROM writing_prompts WHERE id=?", (prompt_id,))
            row = c.fetchone()
            prompt_text = row[0] if row else "お題が取得できませんでした"

        score, feedback, correct_example = evaluate_writing(prompt_text, answer)

        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO writing_answers (user_id, prompt_id, answer, score, feedback, correct_example, attempt_date)
                VALUES (?,?,?,?,?,?,?)
            """, (user_id, prompt_id, answer, score, feedback, correct_example, datetime.datetime.now().isoformat()))
            conn.commit()

        return jsonify({
            "score": score,
            "feedback": feedback,
            "correct_example": correct_example
        })
    except Exception as e:
        print("api_submit_writing error:", e)
        return jsonify({"error":"internal server error"}), 500

# -----------------------
# 従来ルーティング（画面表示用）
# -----------------------
@app.route("/")
@app.route("/index")
def index():
    return render_template("index.html",
                           username=session.get("username","ゲスト"),
                           is_guest=session.get("is_guest", False))

@app.route("/word_quiz")
def word_quiz():
    user_id = session.get("user_id", 0)
    word_data = get_random_word()
    if not word_data:
        flash("単語が登録されていません。")
        return redirect(url_for("index"))
    word_id, word, definition_ja = word_data
    return render_template("word_quiz.html",
                           word_id=word_id,
                           word=word,
                           average_score=get_average_score(user_id),
                           username=session.get("username","ゲスト"),
                           is_guest=session.get("is_guest", False))

@app.route("/writing_quiz")
def writing_quiz():
    user_id = session.get("user_id", 0)
    prompt = get_random_prompt()
    return render_template("writing_quiz.html",
                           prompt=prompt["text"],
                           prompt_id=prompt["id"],
                           user_id=user_id,
                           username=session.get("username","ゲスト"),
                           is_guest=session.get("is_guest", False))

@app.route("/health")
def health():
    return "OK", 200

# -----------------------
# ローカル起動
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting local Flask server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)
