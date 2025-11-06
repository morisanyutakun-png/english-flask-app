from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import datetime
import json
import re
import os

# Colabでは.env読み込みより直接APIキー設定推奨
GEMINI_API_KEY = "YOUR_GEMINI_2_5_KEY"  # Colabでは直接設定でもOK

# -----------------------
# Flask 初期化
# -----------------------
app = Flask(__name__)
app.secret_key = "dev_secret_for_colab"

# -----------------------
# DB 設定（Colabでは /content/tmp に置く）
# -----------------------
TMP_DIR = "/content/tmp"
if not os.path.exists(TMP_DIR):
    os.makedirs(TMP_DIR, exist_ok=True)

DB_FILE = os.path.join(TMP_DIR, "english_learning.db")
WRITING_DB = os.path.join(TMP_DIR, "writing_quiz.db")

# -----------------------
# Gemini 設定
# -----------------------
HAS_GEMINI = False
try:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    HAS_GEMINI = True
except Exception as e:
    print("⚠️ Gemini init failed:", e)
    HAS_GEMINI = False

# -----------------------
# DB 初期化関数（元コードそのまま）
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
            wrong_count INTEGER DEFAULT 0
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
# 採点関数（Gemini 2.5 Flash使用）
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
# ルーティング
# -----------------------
@app.route("/")
@app.route("/index")
def index():
    return render_template("index.html",
                           username=session.get("username","ゲスト"),
                           is_guest=session.get("is_guest", False))

@app.route("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。")
    return redirect(url_for("index"))

@app.route("/word_quiz")
def word_quiz():
    review = request.args.get("review", default=0, type=int)
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
                           is_guest=session.get("is_guest", False),
                           review=review)

@app.route("/submit_answer", methods=["POST"])
def submit_answer():
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
            "average_score": get_average_score(user_id)
        })
    except Exception as e:
        print("submit_answer error:", e)
        return jsonify({"error": "internal server error"}), 500

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

@app.route("/submit_writing", methods=["POST"])
def submit_writing():
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

        return render_template("writing_result.html",
                               prompt=prompt_text,
                               answer=answer,
                               score=score,
                               feedback=feedback,
                               correct_example=correct_example,
                               username=session.get("username","ゲスト"))
    except Exception as e:
        print("submit_writing error:", e)
        flash("サーバーエラーが発生しました。")
        return redirect(url_for("writing_quiz"))

# -----------------------
# ★追加：rankingルート（エラー防止）
# -----------------------
@app.route("/ranking")
def ranking():
    # とりあえずプレースホルダとしてindexにリダイレクト
    flash("ランキング機能は準備中です。")
    return redirect(url_for("index"))

@app.route("/health")
def health():
    return "OK", 200

# -----------------------
# Colab 上で Ngrok を使って起動
# -----------------------
if __name__ == "__main__":
    from pyngrok import ngrok

    port = 5000
    public_url = ngrok.connect(port)
    print("🔥 Ngrok URL:", public_url)

    app.run(port=port)