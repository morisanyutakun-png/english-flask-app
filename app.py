# studyST/app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import datetime
import json
import re
import os
from dotenv import load_dotenv

# ローカルで .env を使う場合に読み込む（Renderでは環境変数を直接設定すること）
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_secret_for_local_only")

# -----------------------
# DB 設定（優先順: repo DB -> /tmp に作る）
# -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# リポジトリにコミットしたDBファイル名（もしあれば）
REPO_DB_FILE = os.path.join(BASE_DIR, "english_learning.db")
REPO_WRITING_DB = os.path.join(BASE_DIR, "writing_quiz.db")

# デフォルトは /tmp（Render で書き込み可能）
TMP_DIR = "/tmp"
DB_DIR = os.getenv("DB_DIR", TMP_DIR)
if not os.path.exists(DB_DIR):
    try:
        os.makedirs(DB_DIR, exist_ok=True)
    except Exception as e:
        print("Warning: couldn't create DB_DIR:", e)

DB_FILE = REPO_DB_FILE if os.path.exists(REPO_DB_FILE) else os.path.join(DB_DIR, "english_learning.db")
WRITING_DB = REPO_WRITING_DB if os.path.exists(REPO_WRITING_DB) else os.path.join(DB_DIR, "writing_quiz.db")

# -----------------------
# Gemini ラッパー（あれば）
# -----------------------
HAS_GEMINI = False
try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        HAS_GEMINI = True
    else:
        print("GEMINI_API_KEY not set; running without Gemini.")
except Exception as e:
    print("google.generativeai not available or failed to init:", e)
    HAS_GEMINI = False

# -----------------------
# DB 初期化
# -----------------------
def init_db_file(path, create_statements):
    # connect will create file if not exists
    with sqlite3.connect(path) as conn:
        c = conn.cursor()
        for stmt in create_statements:
            c.execute(stmt)
        conn.commit()

def init_all_dbs():
    try:
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
        print("DBs initialized:", DB_FILE, WRITING_DB)
    except Exception as e:
        print("DB initialization failed:", e)

# 起動時に DB を初期化（repo に DB を置いた場合は既存DBを使う）
init_all_dbs()

# -----------------------
# helper: JSONパース（Gemini用）
# -----------------------
def parse_json_from_text(text):
    match = re.search(r'(\{(?:[^{}]|(?R))*\})', text, re.DOTALL)
    if not match:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end+1]
        else:
            return None
    else:
        snippet = match.group(1)
    try:
        return json.loads(snippet)
    except Exception as e:
        print("JSON parse error:", e)
        return None

# -----------------------
# 採点関数（Gemini が無ければ簡易実装）
# -----------------------
def evaluate_answer(word, correct_meaning, user_answer):
    if not HAS_GEMINI:
        score = 100 if user_answer.strip() and correct_meaning in user_answer else 60
        feedback = "（簡易採点）" + ("Good!" if score >= 70 else "もう少し詳しく書いてみよう")
        example = f"Example: {word} is used like ... "
        pos = ""
        simple_meaning = correct_meaning or ""
        return score, feedback, example, pos, simple_meaning

    prompt = f"""
あなたは英語教師です。
単語: {word}
正しい意味（日本語）: {correct_meaning}
学習者の回答（日本語）: {user_answer}

JSONで出力:
{{"score":0,"feedback":"...","example":"...","pos":"...","simple_meaning":"..."}}
"""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        res = model.generate_content(prompt)
        text = getattr(res, "text", "") or str(res)
        data = parse_json_from_text(text)
        if data:
            return int(data.get("score",0)), data.get("feedback",""), data.get("example",""), data.get("pos",""), data.get("simple_meaning","")
        else:
            return 0, "採点できませんでした（解析失敗）。", "", "", ""
    except Exception as e:
        print("Gemini Error:", e)
        return 0, "採点中にエラーが発生しました。", "", "", ""

def evaluate_writing(prompt_text, answer):
    if not HAS_GEMINI:
        score = 80 if len(answer.split()) > 3 else 30
        return score, "（簡易採点）改善点を確認してください", "This is an example."
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        res = model.generate_content(f"お題:{prompt_text}\n回答:{answer}\nJSONで返して")
        text = getattr(res, "text", "") or str(res)
        data = parse_json_from_text(text)
        if data:
            return int(data.get("score",0)), data.get("feedback",""), data.get("correct_example","")
        else:
            return 0, "採点できませんでした", ""
    except Exception as e:
        print("Gemini writing error:", e)
        return 0, "採点中にエラーが発生しました。", ""

# -----------------------
# DB 操作ユーティリティ
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
    # トップページではログインやクイズへのボタンを表示するテンプレを使う前提
    return render_template("index.html",
                           username=session.get("username","ゲスト"),
                           is_guest=session.get("is_guest", False))

@app.route("/word_quiz")
def word_quiz():
    # review パラメータ optional
    review = request.args.get("review", default=0, type=int)
    user_id = session.get("user_id", 0)
    word_data = get_random_word()
    if word_data:
        word_id, word, definition_ja = word_data[0], word_data[1], word_data[2]
        return render_template("word_quiz.html",
                               word_id=word_id,
                               word=word,
                               average_score=get_average_score(user_id),
                               username=session.get("username","ゲスト"),
                               is_guest=session.get("is_guest", False),
                               review=review)
    else:
        # DBに単語がなければフラッシュしてトップへ戻す
        flash("単語が登録されていません。管理画面で登録してください。")
        return redirect(url_for("index"))

@app.route("/submit_answer", methods=["POST"])
def submit_answer():
    # このエンドポイントは JS fetch から呼ばれる想定
    try:
        user_id = session.get("user_id", 0)
        word_id = request.form.get("word_id")
        answer = request.form.get("answer", "")
        review = int(request.form.get("review", 0) or 0)

        if not word_id:
            return jsonify({"error":"word_id missing"}), 400

        # 単語取得
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT word, definition_ja FROM words WHERE id=?", (word_id,))
            row = c.fetchone()
            if not row:
                return jsonify({"error":"単語が見つかりません"}), 404
            word, correct_meaning = row[0], row[1]

        # 採点
        score, feedback, example, pos, simple_meaning = evaluate_answer(word, correct_meaning, answer)

        # DB保存（例外処理あり）
        try:
            with sqlite3.connect(DB_FILE) as conn:
                c = conn.cursor()
                c.execute("SELECT wrong_count FROM student_answers WHERE user_id=? AND word_id=?", (user_id, word_id))
                existing = c.fetchone()
                wrong_count = (existing[0] + 1) if existing else (1 if score < 70 else 0)
                c.execute("""
                    INSERT INTO student_answers (user_id, word_id, score, feedback, example, attempt_date, is_wrong, wrong_count)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (user_id, word_id, score, feedback, example, datetime.datetime.now().isoformat(), 1 if score<70 else 0, wrong_count))
                conn.commit()
        except Exception as e:
            print("DB Error on insert student_answers:", e)
            # 保存失敗してもAPIは採点結果を返す（ユーザ体験を優先）
            # ここではログに出して続行

        resp = {
            "score": score,
            "feedback": feedback,
            "example": example,
            "pos": pos,
            "simple_meaning": simple_meaning,
            "average_score": get_average_score(user_id)
        }
        return jsonify(resp)
    except Exception as e:
        print("submit_answer general error:", e)
        return jsonify({"error":"internal server error"}), 500

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

        # fetch prompt text
        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT prompt_text FROM writing_prompts WHERE id=?", (prompt_id,))
            row = c.fetchone()
            prompt_text = row[0] if row else "お題が取得できませんでした"

        score, feedback, correct_example = evaluate_writing(prompt_text, answer)

        # Save attempt
        try:
            with sqlite3.connect(WRITING_DB) as conn:
                c = conn.cursor()
                c.execute("SELECT wrong_count FROM writing_answers WHERE user_id=? AND prompt_id=?", (user_id, prompt_id))
                existing = c.fetchone()
                wrong_count = (existing[0] + 1) if existing else (1 if score < 50 else 0)
                c.execute("""
                    INSERT INTO writing_answers (user_id, prompt_id, answer, score, feedback, correct_example, attempt_date, is_wrong, wrong_count)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (user_id, prompt_id, answer, score, feedback, correct_example, datetime.datetime.now().isoformat(), 1 if score<50 else 0, wrong_count))
                conn.commit()
        except Exception as e:
            print("DB Error writing save:", e)

        return render_template("writing_result.html",
                               prompt=prompt_text,
                               answer=answer,
                               score=score,
                               feedback=feedback,
                               correct_example=correct_example,
                               username=session.get("username","ゲスト"),
                               is_guest=session.get("is_guest", False))
    except Exception as e:
        print("submit_writing error:", e)
        flash("サーバーエラーが発生しました。")
        return redirect(url_for("writing_quiz"))

# simple routes for navigation sanity
@app.route("/health")
def health():
    return "OK", 200

# -----------------------
# ローカル起動
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
