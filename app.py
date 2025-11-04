# studyST/app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import datetime
import json
import re
import os
from dotenv import load_dotenv

# ===== 環境変数読み込み（ローカル用） =====
load_dotenv()

# ===== Flask 初期化 =====
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super_secret_key_dev_only")

# ===== DB パス設定（Render向けに /tmp をデフォルト） =====
# Render のファイルシステムはデプロイごとにリセットされます。 /tmp は書き込み可能。
DB_DIR = os.getenv("DB_DIR", "/tmp")  # 必要なら Render の環境変数で上書き
if not os.path.exists(DB_DIR):
    try:
        os.makedirs(DB_DIR, exist_ok=True)
    except Exception as e:
        print("Warning: DB_DIR create failed:", e)

DB_FILE = os.path.join(DB_DIR, "english_learning.db")
WRITING_DB = os.path.join(DB_DIR, "writing_quiz.db")

# ===== google.generativeai (Gemini) の安全な取り扱い =====
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except Exception as e:
    print("Info: google.generativeai not available:", e)
    HAS_GEMINI = False

if HAS_GEMINI:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
        except Exception as e:
            print("Warning: genai.configure() failed:", e)
            HAS_GEMINI = False
    else:
        print("Warning: GEMINI_API_KEY not set; Gemini calls will be disabled.")
        HAS_GEMINI = False

# ===== DB 初期化関数 =====
def init_db_file(path, create_statements):
    try:
        # connect will create the file if not exists
        with sqlite3.connect(path) as conn:
            c = conn.cursor()
            for stmt in create_statements:
                c.execute(stmt)
            conn.commit()
    except Exception as e:
        print(f"Error initializing DB {path}:", e)
        raise

def init_db():
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
    init_db_file(DB_FILE, create_users_words)

def init_writing_db():
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
    init_db_file(WRITING_DB, create_writing)

# 初期化（起動時に実行）
try:
    init_db()
    init_writing_db()
except Exception as e:
    print("DB initialization failed:", e)

# ===== Gemini 採点（英単語） =====
def parse_json_from_text(text):
    """
    テキストから最初に現れるJSONオブジェクトを抽出して返す。
    """
    match = re.search(r'(\{(?:[^{}]|(?R))*\})', text, re.DOTALL)
    if not match:
        # フォールバック: 最初の { から最後の } までを取る（粗い）
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

def evaluate_answer(word, correct_meaning, user_answer):
    # Gemini 利用不可なら簡易採点を返す
    if not HAS_GEMINI:
        score = 100 if user_answer.strip() and correct_meaning in user_answer else 50
        feedback = "（簡易採点）" + ("良い回答です" if score >= 70 else "改善の余地あり")
        example = f"Example sentence using {word}."
        pos = "n/a"
        simple_meaning = correct_meaning
        return score, feedback, example, pos, simple_meaning

    prompt = f"""
あなたは英語教師です。
以下の情報だけを使って、学習者の回答を採点してください。

- 単語: {word}
- 正しい意味（日本語）: {correct_meaning}
- 学習者の回答（日本語）: {user_answer}

出力はJSON形式で:

{{
  "score": 0-100,
  "feedback": "学習者への簡単なアドバイス（日本語）",
  "example": "単語を使った例文（英語）",
  "pos": "名詞/動詞/形容詞など",
  "simple_meaning": "簡易的な意味（日本語）"
}}
"""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        res = model.generate_content(prompt)
        text = getattr(res, "text", "") or str(res)
        data = parse_json_from_text(text)
        if data:
            return (
                int(data.get("score", 0)),
                data.get("feedback", ""),
                data.get("example", ""),
                data.get("pos", ""),
                data.get("simple_meaning", "")
            )
        else:
            return 0, "採点できませんでした（解析失敗）。", "", "", ""
    except Exception as e:
        print("Gemini Error (evaluate_answer):", e)
        return 0, "採点中にエラーが発生しました。", "", "", ""

# ===== Gemini 採点（英作文） =====
def evaluate_writing(prompt_text, answer):
    if not HAS_GEMINI:
        score = 80 if len(answer.split()) > 3 else 30
        feedback = "（簡易採点）語順や表現をチェックしてください。"
        correct_example = "This is an example."
        return score, feedback, correct_example

    prompt_text_for_gemini = f"""
あなたは英語教師です。
次の日本語文を英語に翻訳する課題に対する学習者の回答を採点してください。

【お題（日本語）】
{prompt_text}

【学習者の英作文】
{answer}

以下の形式でJSONを返してください:

{{
  "score": 0-100,
  "feedback": "改善点などを日本語で簡潔に述べる",
  "correct_example": "自然で正しい英文"
}}
"""
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        res = model.generate_content(prompt_text_for_gemini)
        text = getattr(res, "text", "") or str(res)
        data = parse_json_from_text(text)
        if data:
            return (
                int(data.get("score", 0)),
                data.get("feedback", "フィードバックなし"),
                data.get("correct_example", "")
            )
        else:
            return 0, "採点できませんでした（解析失敗）。", ""
    except Exception as e:
        print("Gemini Error (evaluate_writing):", e)
        return 0, "エラーが発生しました。", ""

# ===== DB 操作関数（絶対パスで接続） =====
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
            avg = c.fetchone()[0]
            return round(avg, 2) if avg else 0
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
                return {"id": row[0], "text": row[1]}
    except Exception as e:
        print("DB Error get_random_prompt:", e)
    return {"id": None, "text": "お題が見つかりませんでした"}

# ===== ルーティング（ほぼ既存） =====
@app.route("/")
@app.route("/index")
def index():
    return render_template(
        "index.html",
        username=session.get("username", "ゲスト"),
        is_guest=session.get("is_guest", False),
        user_authenticated="user_id" in session
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        try:
            with sqlite3.connect(DB_FILE) as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM users WHERE username=? AND password=?", (username, password))
                user = c.fetchone()
            if user:
                session["user_id"] = user[0]
                session["username"] = username
                session["is_guest"] = False
                return redirect(url_for("index"))
            else:
                error = "ユーザー名またはパスワードが違います"
        except Exception as e:
            print("DB Error login:", e)
            error = "サーバーエラーが発生しました"
    return render_template("login.html", error=error)

@app.route("/guest-login", methods=["POST"])
def guest_login():
    session["user_id"] = 0
    session["username"] = "guest"
    session["is_guest"] = True
    return redirect(url_for("index"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if not username or not password:
            error = "ユーザー名とパスワードを入力してください"
        else:
            try:
                with sqlite3.connect(DB_FILE) as conn:
                    c = conn.cursor()
                    c.execute("INSERT INTO users (username,password) VALUES (?,?)", (username, password))
                    conn.commit()
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                error = "このユーザー名はすでに使われています"
            except Exception as e:
                print("DB Error register:", e)
                error = "サーバーエラーが発生しました"
    return render_template("register.html", error=error)

@app.route("/word_quiz")
def word_quiz():
    user_id = session.get("user_id", 0)
    word_data = get_random_word()
    if word_data:
        return render_template(
            "word_quiz.html",
            word_id=word_data[0],
            word=word_data[1],
            average_score=get_average_score(user_id),
            username=session.get("username", "ゲスト"),
            is_guest=session.get("is_guest", False)
        )
    else:
        flash("単語が登録されていません")
        return redirect(url_for("index"))

@app.route("/submit_answer", methods=["POST"])
def submit_answer():
    user_id = session.get("user_id", 0)
    word_id = request.form.get("word_id")
    answer = request.form.get("answer", "")
    review = int(request.form.get("review", 0))

    # 単語取得
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT word, definition_ja FROM words WHERE id=?", (word_id,))
            row = c.fetchone()
            if not row:
                return jsonify({"error": "単語が見つかりません"}), 404
            word, correct_meaning = row
    except Exception as e:
        print("DB Error submit_answer fetch word:", e)
        return jsonify({"error": "サーバーエラー"}), 500

    # 採点
    score, feedback, example, pos, simple_meaning = evaluate_answer(word, correct_meaning, answer)

    # wrong判定
    is_wrong = 1 if score < 70 else 0

    # DB保存
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT wrong_count FROM student_answers WHERE user_id=? AND word_id=?", (user_id, word_id))
            existing = c.fetchone()
            wrong_count = (existing[0] + 1) if existing else (1 if is_wrong else 0)
            c.execute("""
                INSERT INTO student_answers (user_id, word_id, score, feedback, example, attempt_date, is_wrong, wrong_count)
                VALUES (?,?,?,?,?,?,?,?)
            """, (user_id, word_id, score, feedback, example, datetime.datetime.now().isoformat(), is_wrong, wrong_count))
            conn.commit()
    except Exception as e:
        print("DB Error submit_answer insert:", e)
        return jsonify({"error": "サーバーエラー"}), 500

    return jsonify({
        "score": score,
        "feedback": feedback,
        "example": example,
        "pos": pos,
        "simple_meaning": simple_meaning,
        "average_score": get_average_score(user_id)
    })

@app.route("/writing_quiz")
def writing_quiz():
    user_id = session.get("user_id", 0)
    prompt_data = get_random_prompt()
    return render_template(
        "writing_quiz.html",
        prompt=prompt_data["text"],
        prompt_id=prompt_data["id"],
        user_id=user_id,
        username=session.get("username", "ゲスト"),
        is_guest=session.get("is_guest", False)
    )

@app.route("/submit_writing", methods=["POST"])
def submit_writing():
    user_id = request.form.get("user_id", 0)
    prompt_id = request.form.get("prompt_id")
    answer = request.form.get("answer", "")
    review_mode = int(request.form.get("review_mode", 0))

    print("=== submit_writing START ===")
    print("user_id:", user_id, "prompt_id:", prompt_id)

    # 正しいお題をDBから取得
    try:
        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT prompt_text FROM writing_prompts WHERE id=?", (prompt_id,))
            row = c.fetchone()
            prompt_text = row[0] if row else "お題が取得できませんでした"
    except Exception as e:
        print("DB Error submit_writing fetch prompt:", e)
        flash("サーバーエラーが発生しました。")
        return redirect(url_for("writing_quiz"))

    # 採点
    try:
        print("採点開始")
        score, feedback, correct_example = evaluate_writing(prompt_text, answer)
        print("採点完了:", score)
    except Exception as e:
        print("採点エラー:", e)
        flash("採点中にエラーが発生しました。")
        return redirect(url_for("writing_quiz"))

    is_wrong = 1 if score < 50 else 0

    # DB保存
    try:
        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT wrong_count FROM writing_answers WHERE user_id=? AND prompt_id=?", (user_id, prompt_id))
            existing = c.fetchone()
            wrong_count = (existing[0] + 1) if existing else (1 if is_wrong else 0)
            c.execute("""
                INSERT INTO writing_answers (user_id, prompt_id, answer, score, feedback, correct_example, attempt_date, is_wrong, wrong_count)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (user_id, prompt_id, answer, score, feedback, correct_example,
                  datetime.datetime.now().isoformat(), is_wrong, wrong_count))
            conn.commit()
    except Exception as e:
        print("DB Error submit_writing insert:", e)
        flash("サーバーエラーが発生しました。")
        return redirect(url_for("writing_quiz"))

    # 結果ページに遷移
    return render_template(
        "writing_result.html",
        prompt=prompt_text,
        answer=answer,
        score=score,
        feedback=feedback,
        correct_example=correct_example,
        username=session.get("username", "ゲスト"),
        is_guest=session.get("is_guest", False)
    )

@app.route("/ranking")
def ranking():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT users.username, AVG(student_answers.score) as avg_score
                FROM student_answers
                JOIN users ON student_answers.user_id = users.id
                GROUP BY users.username
                ORDER BY avg_score DESC
                LIMIT 10
            """)
            ranking_data = c.fetchall()
    except Exception as e:
        print("DB Error ranking:", e)
        ranking_data = []
    return render_template("ranking.html", ranking=ranking_data)

@app.route("/add_to_weak", methods=["POST"])
def add_to_weak():
    user_id = request.form.get("user_id")
    prompt_id = request.form.get("prompt_id")
    add_flag = request.form.get("add_to_weak")  # '1' なら追加, '0' なら削除

    try:
        if add_flag == '1':
            message = "この問題を苦手モードに追加しました"
        else:
            message = "この問題を苦手モードから削除しました"

        return jsonify({"success": True, "message": message})
    except Exception as e:
        print("add_to_weak error:", e)
        return jsonify({"success": False, "message": "エラーが発生しました"})

# ===== ローカル起動用 =====
if __name__ == "__main__":
    from os import environ
    port = int(environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
