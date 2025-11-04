from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import google.generativeai as genai
import datetime
import json
import re
import os
from dotenv import load_dotenv

app = Flask(__name__)
app.secret_key = "super_secret_key"

# ===== Gemini 設定 =====
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# ===== データベース設定 =====
DB_FILE = "english_learning.db"
WRITING_DB = "writing_quiz.db"

# ===== DB初期化 =====
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT UNIQUE,
            definition_ja TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS student_answers (
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
        )''')
        conn.commit()

def init_writing_db():
    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS writing_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_text TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS writing_answers (
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
        )''')
        conn.commit()

init_db()
init_writing_db()

# ===== Gemini 採点（英単語用） =====
def evaluate_answer(word, correct_meaning, user_answer):
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
        text = res.text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return (
                int(data.get("score", 0)),
                data.get("feedback", ""),
                data.get("example", ""),
                data.get("pos", ""),
                data.get("simple_meaning", "")
            )
        else:
            return 0, "採点できませんでした。", "", "", ""
    except Exception as e:
        print("Gemini Error:", e)
        return 0, "採点中にエラーが発生しました。", "", "", ""

# ===== Gemini 採点（英作文用） =====
def evaluate_writing(prompt_text, answer):
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
        text = res.text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return (
                int(data.get("score", 0)),
                data.get("feedback", "フィードバックなし"),
                data.get("correct_example", "")
            )
        else:
            return 0, "採点できませんでした。", ""
    except Exception as e:
        print("Gemini Error:", e)
        return 0, "エラーが発生しました。", ""

# ===== DB操作関数 =====
def get_random_word():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, word, definition_ja FROM words ORDER BY RANDOM() LIMIT 1")
        return c.fetchone()

def get_average_score(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT AVG(score) FROM student_answers WHERE user_id=?", (user_id,))
        avg = c.fetchone()[0]
        return round(avg,2) if avg else 0

def get_random_prompt():
    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute("SELECT id, prompt_text FROM writing_prompts ORDER BY RANDOM() LIMIT 1")
        row = c.fetchone()
        if row:
            return {"id": row[0], "text": row[1]}
    return {"id": None, "text": "お題が見つかりませんでした"}

# ===== ルーティング =====
@app.route("/")
@app.route("/index")
def index():
    return render_template(
        "index.html",
        username=session.get("username", "ゲスト"),
        is_guest=session.get("is_guest", False),
        user_authenticated="user_id" in session
    )

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method=="POST":
        username = request.form.get("username")
        password = request.form.get("password")
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE username=? AND password=?", (username,password))
            user = c.fetchone()
        if user:
            session["user_id"] = user[0]
            session["username"] = username
            session["is_guest"] = False
            return redirect(url_for("index"))
        else:
            error = "ユーザー名またはパスワードが違います"
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

@app.route("/register", methods=["GET","POST"])
def register():
    error = None
    if request.method=="POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if not username or not password:
            error = "ユーザー名とパスワードを入力してください"
        else:
            with sqlite3.connect(DB_FILE) as conn:
                c = conn.cursor()
                try:
                    c.execute("INSERT INTO users (username,password) VALUES (?,?)",(username,password))
                    conn.commit()
                    return redirect(url_for("login"))
                except sqlite3.IntegrityError:
                    error = "このユーザー名はすでに使われています"
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
    user_id = session.get("user_id",0)
    word_id = request.form.get("word_id")
    answer = request.form.get("answer","")
    review = int(request.form.get("review",0))

    # 単語取得
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT word, definition_ja FROM words WHERE id=?",(word_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"error":"単語が見つかりません"}),404
        word, correct_meaning = row

    # 採点
    score, feedback, example, pos, simple_meaning = evaluate_answer(word, correct_meaning, answer)

    # wrong判定
    is_wrong = 1 if score < 70 else 0

    # DB保存
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT wrong_count FROM student_answers WHERE user_id=? AND word_id=?",(user_id,word_id))
        existing = c.fetchone()
        wrong_count = (existing[0]+1) if existing else (1 if is_wrong else 0)
        c.execute("""
            INSERT INTO student_answers (user_id, word_id, score, feedback, example, attempt_date, is_wrong, wrong_count)
            VALUES (?,?,?,?,?,?,?,?)
        """,(user_id, word_id, score, feedback, example, datetime.datetime.now().isoformat(), is_wrong, wrong_count))
        conn.commit()

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
    user_id = session.get("user_id",0)
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
    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute("SELECT prompt_text FROM writing_prompts WHERE id=?", (prompt_id,))
        row = c.fetchone()
        prompt_text = row[0] if row else "お題が取得できませんでした"

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
    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute("SELECT wrong_count FROM writing_answers WHERE user_id=? AND prompt_id=?", (user_id, prompt_id))
        existing = c.fetchone()
        wrong_count = (existing[0]+1) if existing else (1 if is_wrong else 0)
        c.execute("""
            INSERT INTO writing_answers (user_id, prompt_id, answer, score, feedback, correct_example, attempt_date, is_wrong, wrong_count)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (user_id, prompt_id, answer, score, feedback, correct_example,
              datetime.datetime.now().isoformat(), is_wrong, wrong_count))
        conn.commit()

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
    return render_template("ranking.html", ranking=ranking_data)


#==弱点追加===
@app.route("/add_to_weak", methods=["POST"])
def add_to_weak():
    user_id = request.form.get("user_id")
    prompt_id = request.form.get("prompt_id")
    add_flag = request.form.get("add_to_weak")  # '1' なら追加, '0' なら削除

    try:
        if add_flag == '1':
            # DBに弱点として登録する処理
            # 例: db.add_to_weak(user_id, prompt_id)
            message = "この問題を苦手モードに追加しました"
        else:
            # DBから削除する処理
            # 例: db.remove_from_weak(user_id, prompt_id)
            message = "この問題を苦手モードから削除しました"

        return jsonify({"success": True, "message": message})
    except Exception as e:
        print(e)
        return jsonify({"success": False, "message": "エラーが発生しました"})

# ===== アプリ起動 =====
# ===== アプリ起動 =====
if __name__ == "__main__":
    # Render環境ではこのブロックは使われない（ローカルデバッグ用）
    from os import environ
    port = int(environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
