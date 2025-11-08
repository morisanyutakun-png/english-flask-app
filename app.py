# studyST/app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import datetime
import json
import os
import logging
import shutil
import re
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
# 品詞マップ (英語キー -> 日本語)
# ======================================================
POS_JA = {
    "adjective": "形容詞",
    "adj": "形容詞",
    "noun": "名詞",
    "n": "名詞",
    "verb": "動詞",
    "v": "動詞",
    "adverb": "副詞",
    "adv": "副詞",
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
# DB 初期化関数
# ======================================================
def init_db_file(path, create_statements):
    with sqlite3.connect(path) as conn:
        c = conn.cursor()
        for stmt in create_statements:
            c.execute(stmt)
        conn.commit()
        logger.info(f"DB initialized: {path}")

def ensure_word_pos_column(path):
    try:
        with sqlite3.connect(path) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(words)")
            cols = [r[1] for r in c.fetchall()]
            if "pos" not in cols:
                logger.info("Adding 'pos' column to words table.")
                c.execute("ALTER TABLE words ADD COLUMN pos TEXT DEFAULT NULL")
                conn.commit()
    except Exception as e:
        logger.error("ensure_word_pos_column error: %s", e)

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
    ensure_word_pos_column(DB_FILE)

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
        return {}

# ======================================================
# 品詞正規化
# ======================================================
def normalize_pos_string(raw):
    if not raw:
        return "その他"
    parts = re.split(r"[,\u3001/\\\s]+", str(raw).strip().lower())
    mapped = []
    for p in parts:
        if not p:
            continue
        token = re.match(r"[a-z]+", p)
        key = token.group(0) if token else p
        ja = POS_JA.get(key, None)
        if ja and ja not in mapped:
            mapped.append(ja)
    return "・".join(mapped) if mapped else "その他"

# ======================================================
# 英作文お題管理
# ======================================================
PROMPTS = {
    1: {
        "prompt": "九九艦爆の後継機として1943年から戦線に投入され、急降下爆撃により連合軍艦艇に脅威を与えた",
        "example": "The successor of the Kyu-Kyu Bomber was introduced in 1943 and threatened Allied ships with dive bombing.",
        "meaning": "後継機、投入、急降下爆撃"
    },
    2: {
        "prompt": "My greatest wish is to see the world.",
        "example": "My greatest wish is to see the world.",
        "meaning": "願望、願う"
    },
}

# ======================================================
# 英作文採点
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
# ランダム単語 / お題取得
# ======================================================
def get_random_word():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(words)")
            cols = [r[1] for r in c.fetchall()]
            if "pos" in cols:
                c.execute("SELECT id, word, definition_ja, pos FROM words ORDER BY RANDOM() LIMIT 1")
                row = c.fetchone()
                if row:
                    return row
            c.execute("SELECT id, word, definition_ja FROM words ORDER BY RANDOM() LIMIT 1")
            row = c.fetchone()
            return row if row else None
    except Exception as e:
        logger.error("DB get_random_word error: %s", e)
        return None

def get_average_score(user_id):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT AVG(score) FROM student_answers WHERE user_id=?", (user_id,))
            r = c.fetchone()
            return round(r[0],2) if r and r[0] else 0
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
# 各ページ & 認証
# ======================================================
@app.route("/")
@app.route("/index")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template(
        "index.html",
        username=session.get("username", "ゲスト"),
        is_guest=session.get("is_guest", False)
    )

@app.route("/writing_quiz")
def writing_quiz():
    user_id = session.get("user_id", 0)
    review_mode = request.args.get("review") == "1"
    prompt = get_random_prompt()
    current_user = {"is_authenticated": bool(session.get("user_id"))}

    return render_template(
        "writing_quiz.html",
        prompt=prompt["text"],
        prompt_id=prompt["id"],
        user_id=user_id,
        is_guest=session.get("is_guest", False),
        review_mode=review_mode,
        current_user=current_user
    )

@app.route("/submit_writing", methods=["POST"])
def submit_writing():
    try:
        user_answer = request.form.get("answer", "").strip()
        try:
            prompt_id = int(request.form.get("prompt_id") or 1)
        except Exception:
            prompt_id = 1

        user_id = session.get("user_id", 0)
        is_guest = session.get("is_guest", True)

        # prompt_id に基づいて模範回答と意味を取得
        prompt_data = PROMPTS.get(prompt_id, PROMPTS[1])
        prompt_text = prompt_data["prompt"]
        correct_example = prompt_data["example"]
        correct_meaning = prompt_data["meaning"]

        # 採点
        if not user_answer:
            score = 0
            feedback = "回答が入力されていません。"
        else:
            score = min(100, len(user_answer) * 2)
            if score >= 90:
                feedback = "非常に良く書けています！文法も自然で読みやすいです。"
            elif score >= 60:
                feedback = "良い回答です。少しだけ自然な言い回しを意識しましょう。"
            else:
                feedback = "改善の余地があります。基本的な文法と語彙を見直してみましょう。"

        session['writing_result'] = {
            "score": score,
            "prompt": prompt_text,
            "answer": user_answer,
            "correct_example": correct_example,
            "correct_meaning": correct_meaning,
            "feedback": feedback,
            "user_id": user_id,
            "prompt_id": prompt_id,
            "is_guest": is_guest,
            "added_to_weak": False
        }

        return redirect(url_for("writing_result"))

    except Exception as e:
        logger.exception("submit_writing error")
        flash("採点中にエラーが発生しました。")
        return redirect(url_for("writing_quiz"))

@app.route("/writing_result")
def writing_result():
    result = session.get('writing_result')
    if not result:
        flash("表示する結果がありません。")
        return redirect(url_for("writing_quiz"))

    return render_template("writing_result.html", **result)

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
