from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import google.generativeai as genai
import datetime
import json
import re
import os
from dotenv import load_dotenv  # ← これが必要！

app = Flask(__name__)
app.secret_key = "super_secret_key"

# ===== Gemini 設定 =====
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# ===== データベース設定 =====
# 英単語クイズ・学習履歴
DB_FILE = "english_learning.db"

# 英作文などのライティングモード用
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
        c.execute("DROP TABLE IF EXISTS writing_answers")
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
def evaluate_writing(prompt, answer):
    prompt_text = f"""
あなたは英語教師です。
次の日本語文を英語に翻訳する課題に対する学習者の回答を採点してください。

【お題（日本語）】
{prompt}

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
        res = model.generate_content(prompt_text)
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

# ===== ランダム単語取得 =====
def get_random_word():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, word, definition_ja FROM words ORDER BY RANDOM() LIMIT 1")
        return c.fetchone()

# ===== 苦手単語取得 =====
def get_review_words(user_id, limit=5):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT words.id, words.word, words.definition_ja
            FROM student_answers
            JOIN words ON student_answers.word_id = words.id
            WHERE student_answers.user_id=? AND student_answers.is_wrong=1
            ORDER BY student_answers.wrong_count DESC
            LIMIT ?
        """, (user_id, limit))
        return c.fetchall()

# ===== ランダムお題取得 =====
def get_random_prompt():
    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute("SELECT id, prompt_text FROM writing_prompts ORDER BY RANDOM() LIMIT 1")
        row = c.fetchone()
        if row:
            return {"id": row[0], "text": row[1]}
    return {"id": None, "text": "お題が見つかりませんでした"}

# ===== 苦手英作文取得 =====
def get_review_writing(user_id, limit=5):
    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT writing_prompts.id, writing_prompts.prompt_text
            FROM writing_answers
            JOIN writing_prompts ON writing_answers.prompt_id = writing_prompts.id
            WHERE writing_answers.user_id=? AND writing_answers.is_wrong=1
            ORDER BY writing_answers.wrong_count DESC
            LIMIT ?
        """, (user_id, limit))
        return c.fetchall()

# ===== 平均スコア =====
def get_average_score(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT AVG(score) FROM student_answers WHERE user_id=?", (user_id,))
        avg = c.fetchone()[0]
        return round(avg, 2) if avg else 0

# ===== ログイン =====
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
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
    return render_template("login.html", error=error)

# ===== ゲストログイン =====
@app.route("/guest-login", methods=["POST"])
def guest_login():
    session["user_id"] = 0
    session["username"] = "guest"
    session["is_guest"] = True
    return redirect(url_for("index"))

# ===== トップページ =====
@app.route('/')
@app.route('/index')
def index():
    username = session.get('username', 'ゲスト')
    # ゲスト判定
    is_guest = session.get('is_guest', False) or username == 'ゲスト'
    return render_template('index.html', username=username, is_guest=is_guest)

# ===== 単語クイズ画面 =====
@app.route("/word_quiz")
def word_quiz():
    if not session.get("username"):
        return redirect(url_for("login"))

    if not session.get("is_guest") and request.args.get("review") == "1":
        words = get_review_words(session["user_id"])
        if words:
            word_id, word, definition_ja = words[0]
        else:
            word_data = get_random_word()
            word_id, word, definition_ja = word_data
    else:
        word_data = get_random_word()
        if not word_data:
            return "単語が登録されていません。"
        word_id, word, definition_ja = word_data

    avg = get_average_score(session["user_id"])
    return render_template("word_quiz.html",
                           word=word,
                           word_id=word_id,
                           definition_ja=definition_ja,
                           average_score=avg)

# ===== 英作文クイズ画面 =====
@app.route("/writing_quiz")
def writing_quiz():
    if not session.get("username"):
        return redirect(url_for("login"))

    if not session.get("is_guest") and request.args.get("review") == "1":
        prompts = get_review_writing(session["user_id"])
        if prompts:
            prompt_id, prompt_text = prompts[0]
        else:
            prompt_data = get_random_prompt()
            prompt_id, prompt_text = prompt_data["id"], prompt_data["text"]
    else:
        prompt_data = get_random_prompt()
        prompt_id, prompt_text = prompt_data["id"], prompt_data["text"]

    return render_template("writing_quiz.html",
                           user_id=session.get("user_id"),
                           prompt=prompt_text,
                           prompt_id=prompt_id)

# ===== 単語回答送信 =====
@app.route("/submit_answer", methods=["POST"])
def submit_answer():
    if not session.get("username"):
        return jsonify({"error": "ログインが必要です。"}), 401

    word_id = request.form.get("word_id")
    answer = request.form.get("answer", "").strip()
    if not answer:
        return jsonify({"error": "回答が空です。"}), 400

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT word, definition_ja FROM words WHERE id=?", (word_id,))
        row = c.fetchone()

    if not row:
        return jsonify({"error": "単語が見つかりません。"}), 404

    word, correct = row
    score, feedback, example, pos, meaning = evaluate_answer(word, correct, answer)

    # 苦手判定（score < 50）
    is_wrong = 1 if score < 50 else 0  # ← 70 → 50 に変更

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        if not session.get("is_guest"):
            # 前回 wrong_count を取得
            c.execute("SELECT wrong_count FROM student_answers WHERE user_id=? AND word_id=? ORDER BY attempt_date DESC LIMIT 1",
                      (session["user_id"], word_id))
            row = c.fetchone()
            prev_wrong_count = row[0] if row else 0
            new_wrong_count = prev_wrong_count + 1 if is_wrong else prev_wrong_count

            c.execute("""INSERT INTO student_answers
                         (user_id, word_id, score, feedback, example, attempt_date, is_wrong, wrong_count)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (session["user_id"], word_id, score, feedback, example,
                       datetime.datetime.now().isoformat(), is_wrong, new_wrong_count))
            conn.commit()

    return jsonify({
        "score": score,
        "feedback": feedback,
        "example": example,
        "pos": pos,
        "simple_meaning": meaning,
        "average_score": get_average_score(session["user_id"])
    })

# ===== 英作文送信 =====
@app.route("/submit_writing", methods=["POST"])
def submit_writing():
    user_id = request.form.get("user_id")
    prompt_id = request.form.get("prompt_id")
    answer = request.form.get("answer")

    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute("SELECT prompt_text FROM writing_prompts WHERE id=?", (prompt_id,))
        row = c.fetchone()
        prompt_text = row[0] if row else None

    if not prompt_text:
        return "お題が見つかりません。", 404

    score, feedback, correct_example = evaluate_writing(prompt_text, answer)

    # 苦手判定
    is_wrong = 1 if score < 50 else 0

    with sqlite3.connect(WRITING_DB) as conn:
        c = conn.cursor()
        c.execute("SELECT wrong_count FROM writing_answers WHERE user_id=? AND prompt_id=? ORDER BY attempt_date DESC LIMIT 1",
                  (user_id, prompt_id))
        row = c.fetchone()
        prev_wrong_count = row[0] if row else 0
        new_wrong_count = prev_wrong_count + 1 if is_wrong else prev_wrong_count

        c.execute('''INSERT INTO writing_answers
                     (user_id, prompt_id, answer, score, feedback, correct_example, attempt_date, is_wrong, wrong_count)
                     VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), ?, ?)''',
                  (user_id, prompt_id, answer, score, feedback, correct_example, is_wrong, new_wrong_count))
        conn.commit()

    return render_template("writing_result.html",
                           prompt=prompt_text,
                           answer=answer,
                           score=score,
                           feedback=feedback,
                           correct_example=correct_example)

# ===== ランキング =====
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

# ===== ログアウト =====
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # 登録処理
        ...
    return render_template('register.html')

# ===== 苦手モード登録用 =====
@app.route('/add_to_weak', methods=['POST'])
def add_to_weak():
    user_id = request.form.get('user_id')
    prompt_id = request.form.get('prompt_id')
    
    if user_id and prompt_id:
        # DBに苦手モード登録（例: writing_answers テーブルの is_wrong を 1 に更新）
        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("""
                UPDATE writing_answers
                SET is_wrong=1
                WHERE user_id=? AND prompt_id=?
            """, (user_id, prompt_id))
            # 該当データがなければ追加する場合
            if c.rowcount == 0:
                c.execute("""
                    INSERT INTO writing_answers
                    (user_id, prompt_id, answer, score, feedback, correct_example, attempt_date, is_wrong, wrong_count)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), 1, 1)
                """, (user_id, prompt_id, "", 0, "", "",))
            conn.commit()
    
    flash("苦手モードに登録しました！")
    return redirect(url_for('writing_quiz'))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
