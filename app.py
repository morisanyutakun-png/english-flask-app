# studyST/app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import datetime
import json
import os
import logging
import shutil
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

# ======================================================
# Flask 初期設定
# ======================================================
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_secret_for_local_only")
CORS(app, origins="*")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ======================================================
# DB 設定（Render / Cloud Run対応）
# ======================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DB_FILE = os.path.join(BASE_DIR, "english_learning.db")
REPO_WRITING_DB = os.path.join(BASE_DIR, "writing_quiz.db")
TMP_DIR = "/tmp"
DB_FILE = os.path.join(TMP_DIR, "english_learning.db")
WRITING_DB = os.path.join(TMP_DIR, "writing_quiz.db")

os.makedirs(TMP_DIR, exist_ok=True)

for src, dst in [(REPO_DB_FILE, DB_FILE), (REPO_WRITING_DB, WRITING_DB)]:
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copy(src, dst)
        logger.info(f"DB copied to tmp: {dst}")

# ======================================================
# Gemini 設定（安全に失敗許容）
# ======================================================
HAS_GEMINI = False
try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        HAS_GEMINI = True
        logger.info("Gemini API configured successfully.")
    else:
        logger.warning("GEMINI_API_KEY not set; Gemini will not be used.")
except Exception as e:
    logger.error("Gemini init failed: %s", e)

# ======================================================
# 品詞マップ
# ======================================================
POS_JA = {
    "adjective": "形容詞",
    "noun": "名詞",
    "verb": "動詞",
    "adverb": "副詞",
    "pronoun": "代名詞",
    "preposition": "前置詞",
    "conjunction": "接続詞",
    "interjection": "間投詞",
    "article": "冠詞",
    "determiner": "限定詞",
    "numeral": "数詞",
    "particle": "助詞",
    "modal": "法助動詞",
    "other": "その他",
}

# ======================================================
# DB 初期化
# ======================================================
def init_db_file(path, create_statements):
    with sqlite3.connect(path) as conn:
        c = conn.cursor()
        for stmt in create_statements:
            c.execute(stmt)
        conn.commit()
        logger.info(f"DB initialized: {path}")

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

    # ゲストユーザー作成
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (id, username, password) VALUES (0,'ゲスト','')")
        conn.commit()

init_all_dbs()

# ======================================================
# JSON 抽出関数
# ======================================================
def parse_json_from_text(text):
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        snippet = text[start:end]
        return json.loads(snippet)
    except Exception:
        logger.warning("JSON parse failed; fallback to empty dict")
        return {}

# ======================================================
# 採点関数
# ======================================================
def evaluate_answer(word, correct_meaning, user_answer):
    if not HAS_GEMINI:
        score = 100 if correct_meaning in user_answer else 60
        return (
            score,
            "（簡易採点）" + ("Good!" if score >= 70 else "もう少し詳しく書いてみよう"),
            f"{word} の使用例（採点対象外）",
            "その他",
            correct_meaning,
        )
    try:
        prompt = f"""
単語: {word}
正しい意味: {correct_meaning}
回答: {user_answer}
JSON形式で返答してください。
{{"score":80,"feedback":"...","example":"...","pos":"...","simple_meaning":"..."}}
"""
        model = genai.GenerativeModel("gemini-2.5-flash")
        res = model.generate_content(prompt)
        data = parse_json_from_text(res.text or "")
        score = max(0, min(100, int(data.get("score", 0))))
        return (
            score,
            data.get("feedback", ""),
            data.get("example", f"{word} の使用例（採点対象外）"),
            POS_JA.get(data.get("pos", "other").lower(), "その他"),
            data.get("simple_meaning", correct_meaning),
        )
    except Exception as e:
        logger.error("Gemini Error: %s", e)
        return 0, "採点エラー", f"{word} の使用例", "その他", correct_meaning

# ======================================================
# Writing採点
# ======================================================
def evaluate_writing(prompt_text, answer):
    if not HAS_GEMINI:
        score = 80 if len(answer.split()) > 3 else 30
        return score, "（簡易採点）改善点を確認してください", "例文は参考"
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        res = model.generate_content(f"お題:{prompt_text}\n回答:{answer}\nJSONで返して")
        data = parse_json_from_text(res.text or "")
        return (
            max(0, min(100, int(data.get("score", 0)))),
            data.get("feedback", ""),
            data.get("correct_example", ""),
        )
    except Exception as e:
        logger.error("Gemini writing error: %s", e)
        return 0, "採点エラー", ""

# ======================================================
# DB操作系
# ======================================================
def get_random_word():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id, word, definition_ja FROM words ORDER BY RANDOM() LIMIT 1")
            return c.fetchone()
    except Exception as e:
        logger.error("DB get_random_word error: %s", e)
        return None

def get_average_score(user_id):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT AVG(score) FROM student_answers WHERE user_id=?", (user_id,))
            r = c.fetchone()
            return round(r[0], 2) if r and r[0] else 0
    except Exception as e:
        logger.error("DB avg error: %s", e)
        return 0

def get_random_prompt():
    try:
        with sqlite3.connect(WRITING_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT id, prompt_text FROM writing_prompts ORDER BY RANDOM() LIMIT 1")
            row = c.fetchone()
            return {"id": row[0], "text": row[1]} if row else {"id": None, "text": "お題がありません"}
    except Exception as e:
        logger.error("DB prompt error: %s", e)
        return {"id": None, "text": "エラー"}

# ======================================================
# 認証
# ======================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id,password FROM users WHERE username=?", (username,))
            row = c.fetchone()
            if row and check_password_hash(row[1], password):
                session.update({"user_id": row[0], "username": username, "is_guest": False})
                return redirect(url_for("index"))
        return render_template("login.html", error="ユーザー名かパスワードが違います")
    return render_template("login.html")

@app.route("/guest_login", methods=["POST"])
def guest_login():
    session.update({"user_id": 0, "username": "ゲスト", "is_guest": True})
    return redirect(url_for("index"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if not username or not password:
            return render_template("register.html", error="必須項目です")
        hashed = generate_password_hash(password)
        try:
            with sqlite3.connect(DB_FILE) as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM users WHERE username=?", (username,))
                if c.fetchone():
                    return render_template("register.html", error="既に登録されています")
                c.execute("INSERT INTO users (username,password) VALUES (?,?)", (username, hashed))
                conn.commit()
                flash("登録完了！ログインしてください")
                return redirect(url_for("login"))
        except Exception as e:
            logger.error("Register Error: %s", e)
            return render_template("register.html", error="登録中にエラー")
    return render_template("register.html")

# ======================================================
# API
# ======================================================
@app.route("/api/submit_answer", methods=["POST"])
def api_submit_answer():
    try:
        user_id = session.get("user_id", 0)
        word_id = request.form.get("word_id")
        answer = request.form.get("answer", "")
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT word,definition_ja FROM words WHERE id=?", (word_id,))
            row = c.fetchone()
            if not row:
                return jsonify({"error": "単語が見つかりません"}), 404
            word, correct_meaning = row
        score, feedback, example, pos, simple_meaning = evaluate_answer(word, correct_meaning, answer)
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO student_answers (user_id,word_id,score,feedback,example,attempt_date)
                   VALUES (?,?,?,?,?,?)""",
                (user_id, word_id, score, feedback, example, datetime.datetime.utcnow().isoformat()),
            )
            conn.commit()
        avg = get_average_score(user_id)
        return jsonify({
            "score": score,
            "feedback": feedback,
            "example": example,
            "pos": pos,
            "simple_meaning": simple_meaning,
            "average_score": avg
        })
    except Exception as e:
        logger.exception("api_submit_answer error")
        return jsonify({"error": "internal server error"}), 500

# ======================================================
# 各ページ
# ======================================================
@app.route("/")
@app.route("/index")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("index.html", username=session.get("username", "ゲスト"))

@app.route("/word_quiz")
def word_quiz():
    user_id = session.get("user_id", 0)
    review = request.args.get("review") == "1"
    word_data = get_random_word()
    if not word_data:
        flash("単語が登録されていません。")
        return redirect(url_for("index"))
    word_id, word, definition_ja = word_data
    return render_template(
        "word_quiz.html",
        word_id=word_id,
        word=word,
        average_score=get_average_score(user_id),
        review=review,
        current_user=session,
    )

@app.route("/writing_quiz")
def writing_quiz():
    user_id = session.get("user_id", 0)
    prompt = get_random_prompt()
    return render_template("writing_quiz.html", prompt=prompt["text"], prompt_id=prompt["id"], user_id=user_id)

@app.route("/ranking")
def ranking():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT u.username, AVG(s.score) as avg_score
                FROM student_answers s
                JOIN users u ON s.user_id = u.id
                GROUP BY u.id
                ORDER BY avg_score DESC LIMIT 10
            """)
            ranking_data = c.fetchall()
    except Exception as e:
        logger.error("Ranking error: %s", e)
        ranking_data = []
    return render_template("ranking.html", ranking=ranking_data)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/health")
def health():
    return "OK", 200

# ======================================================
# ローカル実行
# ======================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)
