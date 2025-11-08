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
# DB 設定
# ======================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DB_FILE = os.path.join(BASE_DIR, "english_learning.db")
REPO_WRITING_DB = os.path.join(BASE_DIR, "writing_quiz.db")
REPO_READING_DB = os.path.join(BASE_DIR, "reding_quiz.db")  # ← 修正
TMP_DIR = "/tmp"
DB_FILE = os.path.join(TMP_DIR, "english_learning.db")
WRITING_DB = os.path.join(TMP_DIR, "writing_quiz.db")
READING_DB = os.path.join(TMP_DIR, "reding_quiz.db")        # ← 修正

os.makedirs(TMP_DIR, exist_ok=True)
for src, dst in [(REPO_DB_FILE, DB_FILE), (REPO_WRITING_DB, WRITING_DB), (REPO_READING_DB, READING_DB)]:
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
# DB 初期化（テーブル作成 + 後方互換で pos カラム追加）
# ======================================================
def init_db_file(path, create_statements):
    with sqlite3.connect(path) as conn:
        c = conn.cursor()
        for stmt in create_statements:
            c.execute(stmt)
        conn.commit()
        logger.info(f"DB initialized: {path}")

def ensure_word_pos_column(path):
    """
    既存DBに pos（品詞）カラムが無ければ追加する。重複追加を避けるためにカラム存在チェックを行う。
    pos には英語キー（例: 'noun','adjective'）を格納する想定。
    """
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
            -- pos カラムは後方互換のため ALTER TABLE で追加される可能性あり
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
    # === READING QUIZ 用テーブル ===
    create_reading = [
        '''CREATE TABLE IF NOT EXISTS reading_passages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            passage TEXT,
            question TEXT,
            correct_answer TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS reading_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            passage_id INTEGER,
            user_answer TEXT,
            score INTEGER,
            feedback TEXT,
            attempt_date TEXT
        )'''
    ]
    init_db_file(DB_FILE, create_users_words)
    init_db_file(WRITING_DB, create_writing)
    init_db_file(READING_DB, create_reading)

    # words テーブルに pos カラムがない場合は追加
    ensure_word_pos_column(DB_FILE)

    # ゲストユーザー作成
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (id, username, password) VALUES (0,'ゲスト','')")
        conn.commit()

init_all_dbs()


# ======================================================
# Gemini 簡易採点関数（リーディング用）
# ======================================================
def evaluate_reading(passage, question, correct_answer, user_answer):
    if not user_answer:
        return 0, "回答が入力されていません。"
    if not HAS_GEMINI:
        score = 100 if correct_answer.strip().lower() in user_answer.strip().lower() else 60
        return score, "（簡易採点）内容を確認してください。"

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
次の英文読解問題の採点をしてください。JSON形式で結果を返してください。

文章:
{passage}

質問:
{question}

正答:
{correct_answer}

学生の回答:
{user_answer}

出力フォーマット:
{{
  "score": 0,
  "feedback": ""
}}
"""
        res = model.generate_content(prompt)
        data = json.loads(re.search(r"\{.*\}", res.text, re.S).group(0))
        score = int(data.get("score", 0))
        feedback = data.get("feedback", "")
        return score, feedback
    except Exception as e:
        logger.error("Gemini reading error: %s", e)
        return 50, "採点に失敗したため簡易スコアを返しました。"

# ======================================================
# Utility
# ======================================================
def get_random_reading():
    try:
        with sqlite3.connect(READING_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT id, title, passage, question, correct_answer FROM reading_passages ORDER BY RANDOM() LIMIT 1")
            row = c.fetchone()
            if row:
                return {"id": row[0], "title": row[1], "passage": row[2], "question": row[3], "correct_answer": row[4]}
    except Exception as e:
        logger.error("DB reading error: %s", e)
    return {"id": None, "title": "エラー", "passage": "", "question": "", "correct_answer": ""}

# ======================================================
# === READING QUIZ ===
# ======================================================
@app.route("/reading_quiz")
def reading_quiz():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session.get("user_id", 0)
    reading = get_random_reading()

    # passage が空なら仮の英文を入れる
    passage_text = reading["passage"] or "This is a sample English passage for practice."
    question_text = reading["question"] or "Please answer the question based on the passage."

    current_user = {"is_authenticated": bool(user_id)}
    return render_template(
        "reading_quiz.html",
        title=reading["title"],
        prompt=passage_text,      # ← ここを prompt に
        question=question_text,
        passage_id=reading["id"],
        user_id=user_id,
        current_user=current_user
    )

@app.route("/submit_reading", methods=["POST"])
def submit_reading():
    try:
        user_id = session.get("user_id", 0)
        passage_id = int(request.form.get("passage_id", 0))
        user_answer = request.form.get("answer", "").strip()

        # DBから問題取得
        with sqlite3.connect(READING_DB) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT title, passage, question, correct_answer FROM reading_passages WHERE id=?",
                (passage_id,)
            )
            row = c.fetchone()

        if not row:
            logger.warning("submit_reading: passage_id %s not found", passage_id)
            flash("問題が見つかりません。")
            return redirect(url_for("reading_quiz"))

        title, passage, question, correct_answer = row

        # passage が空の場合は仮英文を設定
        prompt_text = passage or "This is a sample English passage for practice."
        question_text = question or "Please answer the question based on the passage."
        correct_answer_text = correct_answer or "Sample correct answer."

        # 採点
        score, feedback = evaluate_reading(prompt_text, question_text, correct_answer_text, user_answer)

        # DB保存
        with sqlite3.connect(READING_DB) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO reading_answers
                (user_id, passage_id, user_answer, score, feedback, attempt_date)
                VALUES (?,?,?,?,?,?)
            """, (
                user_id,
                passage_id,
                user_answer,
                score,
                feedback,
                datetime.datetime.utcnow().isoformat()
            ))
            conn.commit()

        # sessionに結果保存
        session["reading_result"] = {
            "title": title,
            "prompt": prompt_text,           # ← HTML側の {{ prompt }} に合わせる
            "question": question_text,
            "user_answer": user_answer,
            "correct_answer": correct_answer_text,
            "score": score,
            "feedback": feedback
        }

        return redirect(url_for("reading_result"))

    except Exception as e:
        logger.exception("submit_reading error")
        flash("採点中にエラーが発生しました。")
        return redirect(url_for("reading_quiz"))


@app.route("/reading_result")
def reading_result():
    result = session.get("reading_result")
    if not result:
        flash("結果がありません。")
        logger.warning("reading_result not found in session")
        return redirect(url_for("reading_quiz"))

    return render_template("reading_result.html", **result)

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
# 品詞文字列正規化関数
# ======================================================
def normalize_pos_string(raw):
    """
    raw: 例 "noun, verb" や "noun/verb" や "Noun Verb" など
    戻り値: "名詞・動詞" のような日本語結合文字列。情報無しなら "その他"
    """
    if not raw:
        return "その他"
    # 小文字化して分割（カンマ、スラッシュ、全角読点、空白などを区切りとする）
    parts = re.split(r"[,\u3001/\\\s]+", str(raw).strip().lower())
    mapped = []
    for p in parts:
        if not p:
            continue
        # もし p が複数語（like "noun (countable)"), take first token before non-alpha
        token = re.match(r"[a-z]+", p)
        key = token.group(0) if token else p
        ja = POS_JA.get(key, None)
        if ja:
            mapped.append(ja)
        else:
            # try to map english full words (e.g., "nounplural") fallback to その他 later
            # skip unknown tokens
            continue
    # dedupe while preserving order
    seen = set()
    result = []
    for x in mapped:
        if x not in seen:
            seen.add(x)
            result.append(x)
    if result:
        return "・".join(result)
    return "その他"

# ======================================================
# 採点関数
# ======================================================
def evaluate_answer(word, correct_meaning, user_answer, pos_from_db=None):
    """
    戻り値:
      score:int,
      feedback:str,
      example: { "en": "...", "jp": "..." },
      pos_ja:str (日本語表記),
      simple_meaning:str
    - pos_from_db: DB に入っている英語キー（例: 'noun'）を渡すと非Gemini時に使う。
    """
    # 非Geminiの簡易採点（フォールバック）
    if not HAS_GEMINI:
        score = 100 if (correct_meaning and correct_meaning in user_answer) else 60
        feedback = "（簡易採点）" + ("Good!" if score >= 70 else "もう少し詳しく書いてみよう")
        example = {"en": f"{word} の使用例（採点対象外）", "jp": ""}
        pos_ja = normalize_pos_string(pos_from_db or "other")
        return score, feedback, example, pos_ja, (correct_meaning or "")

    # Gemini有効時
    try:
        prompt = f"""
単語: {word}
正しい意味: {correct_meaning}
回答: {user_answer}

以下のJSONを必ず返してください（例のフォーマットに従うこと）:
{{
  "score": 95,
  "feedback": "説明テキスト",
  "example": "He gave his assurance that the project would be completed on time.",
  "example_jp": "彼はそのプロジェクトが予定通り完了すると保証した。",
  "pos": "noun, verb",
  "simple_meaning": "保証、確信、自信"
}}
(注意) pos は英語のキーで複数ある場合はカンマ区切りで返してください（例: noun, verb）。
"""
        model = genai.GenerativeModel("gemini-2.5-flash")
        res = model.generate_content(prompt)
        data = parse_json_from_text(res.text or "")

        score = max(0, min(100, int(data.get("score", 0))))
        feedback = data.get("feedback", "") or ""
        example_en = data.get("example", f"{word} の使用例（採点対象外）")
        example_jp = data.get("example_jp", "") or ""
        raw_pos = (data.get("pos") or pos_from_db or "other")
        pos_ja = normalize_pos_string(raw_pos)
        simple_meaning = data.get("simple_meaning", correct_meaning or "")

        example = {"en": example_en, "jp": example_jp}
        return score, feedback, example, pos_ja, simple_meaning
    except Exception as e:
        logger.error("Gemini Error: %s", e)
        example = {"en": f"{word} の使用例", "jp": ""}
        pos_ja = normalize_pos_string(pos_from_db or "other")
        return 0, "採点エラー", example, pos_ja, (correct_meaning or "")

# ======================================================
# Writing採点
# ======================================================
def evaluate_writing(prompt_text, answer):
    """
    Gemini で採点。失敗時は簡易採点にフォールバック。
    戻り値:
      score:int
      feedback:str
      correct_example:str（英語のみ）
    """
    if not answer:
        return 0, "回答が入力されていません。", ""

    # --- Gemini 未使用時 or API Key 無し ---
    if not HAS_GEMINI:
        score = 80 if len(answer.split()) > 3 else 30
        return score, "（簡易採点）改善点を確認してください", "例文は参考"

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
以下の日本語文を英語に翻訳し、必ずJSON形式で返してください。
JSONのキーは以下のみ使用してください：
{{
  "score": 0,
  "feedback": "",
  "correct_example": ""
}}
余計なテキストやお題の日本語は一切含めないでください。

日本語文: {prompt_text}
学生回答: {answer}
"""
        res = model.generate_content(prompt)
        raw_text = res.text or ""
        logger.info("Gemini raw response: %s", raw_text)

        data = parse_json_from_text(raw_text)
        if not data:
            raise ValueError("JSON parse failed or empty")

        score = max(0, min(100, int(data.get("score", 0))))
        feedback = data.get("feedback") or "採点結果なし"
        correct_example = data.get("correct_example") or "模範例文なし"

        # correct_example が dict の場合も文字列化
        if isinstance(correct_example, dict):
            correct_example = correct_example.get("en", "模範例文なし")

        return score, feedback, correct_example

    except Exception as e:
        logger.error("Gemini writing error, fallback to simple scoring: %s", e)
        # 簡易採点
        score = min(100, len(answer.split()) * 10)  # 単語数×10点
        feedback = "採点エラーにより簡易採点を行いました。"
        correct_example = "My greatest wish is to see the world."
        return score, feedback, correct_example

# ======================================================
# DB操作系
# ======================================================
def get_random_word():
    """
    RETURN:
      (id, word, definition_ja, pos_en_or_none)
    pos カラムが存在していれば値を返す（英語キーを想定）。
    """
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(words)")
            cols = [r[1] for r in c.fetchall()]
            if "pos" in cols:
                c.execute("SELECT id, word, definition_ja, pos FROM words ORDER BY RANDOM() LIMIT 1")
                row = c.fetchone()
                if row:
                    return row  # id, word, definition_ja, pos
            c.execute("SELECT id, word, definition_ja FROM words ORDER BY RANDOM() LIMIT 1")
            row = c.fetchone()
            if row:
                return (row[0], row[1], row[2], None)
            return None
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

        # words テーブルから pos も取得する（存在すれば）
        pos_from_db = None
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(words)")
            cols = [r[1] for r in c.fetchall()]
            if "pos" in cols:
                c.execute("SELECT word,definition_ja,pos FROM words WHERE id=?", (word_id,))
                row = c.fetchone()
                if not row:
                    return jsonify({"error": "単語が見つかりません"}), 404
                word, correct_meaning, pos_from_db = row
            else:
                c.execute("SELECT word,definition_ja FROM words WHERE id=?", (word_id,))
                row = c.fetchone()
                if not row:
                    return jsonify({"error": "単語が見つかりません"}), 404
                word, correct_meaning = row

        # 採点（pos_from_db を渡す）
        score, feedback, example, pos_ja, simple_meaning = evaluate_answer(word, correct_meaning, answer, pos_from_db=pos_from_db)

        # student_answers に例文（英語）を保存（互換性のため）
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO student_answers (user_id,word_id,score,feedback,example,attempt_date)
                   VALUES (?,?,?,?,?,?)""",
                (user_id, word_id, score, feedback, example.get("en", ""), datetime.datetime.utcnow().isoformat()),
            )
            conn.commit()

        avg = get_average_score(user_id)
        # フロント向け返却（正解意味は渡さない設計）
        return jsonify({
            "score": score,
            "feedback": feedback,
            "example_en": example.get("en", ""),
            "example_jp": example.get("jp", ""),
            "pos": pos_ja,
            "simple_meaning": simple_meaning,
            "average_score": avg,
            "user_answer": answer
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

    return render_template(
        "index.html",
        username=session.get("username", "ゲスト"),
        is_guest=session.get("is_guest", False)
    )

@app.route("/word_quiz")
def word_quiz():
    user_id = session.get("user_id", 0)
    review = request.args.get("review") == "1"
    word_data = get_random_word()
    if not word_data:
        flash("単語が登録されていません。")
        return redirect(url_for("index"))
    # get_random_word は (id, word, definition_ja, pos_or_none) を返す
    if len(word_data) == 4:
        word_id, word, definition_ja, pos_from_db = word_data
    else:
        word_id, word, definition_ja = word_data
        pos_from_db = None

    # current_user をテンプレ向けに簡易 dict で渡す（テンプレが .is_authenticated を参照するため）
    current_user = {"is_authenticated": bool(session.get("user_id"))}

    return render_template(
        "word_quiz.html",
        word_id=word_id,
        word=word,
        average_score=get_average_score(user_id),
        review=review,
        current_user=current_user,
    )

@app.route("/writing_quiz")
def writing_quiz():
    user_id = session.get("user_id", 0)
    # review フラグを URL パラメータから受け取れるように（例: /writing_quiz?review=1）
    review_mode = request.args.get("review") == "1"
    prompt = get_random_prompt()

    # current_user をテンプレ向けに簡易 dict で渡す（テンプレが .is_authenticated を参照するため）
    current_user = {"is_authenticated": bool(session.get("user_id"))}

    return render_template(
        "writing_quiz.html",
        prompt=prompt["text"],
        prompt_id=prompt["id"],
        user_id=user_id,
        is_guest=session.get("is_guest", False),
        review_mode=review_mode,
        current_user=current_user,
    )

# --- POST: 英作文送信 ---
@app.route("/submit_writing", methods=["POST"])
def submit_writing():
    try:
        # --- ユーザ入力取得 ---
        user_answer = request.form.get("answer", "").strip()
        prompt_text = request.form.get("prompt", "").strip()
        try:
            prompt_id = int(request.form.get("prompt_id") or 0)
        except Exception:
            prompt_id = 0
        user_id = session.get("user_id", 0)
        is_guest = session.get("is_guest", True)

        logger.info(
            "submit_writing called: user_id=%s, prompt_id=%s, answer_len=%d",
            user_id, prompt_id, len(user_answer)
        )

        # --- 採点 ---
        if not user_answer:
            score = 0
            feedback = "回答が入力されていません。"
            correct_example = ""
            correct_meaning = ""
        else:
            try:
                # Gemini 採点を呼ぶ
                score, feedback, correct_example = evaluate_writing(prompt_text, user_answer)
                # correct_example が dict の場合もあるので str に統一
                if isinstance(correct_example, dict):
                    correct_example_text = correct_example.get("en", "")
                else:
                    correct_example_text = correct_example
                correct_example = correct_example_text
                correct_meaning = "願望、願う"  # 必要に応じて Gemini から取得可
            except Exception as e:
                logger.error("Gemini採点失敗: %s", e)
                score = min(100, len(user_answer) * 2)
                feedback = "採点エラーにより簡易採点を行いました。"
                correct_example = "My greatest wish is to see the world."
                correct_meaning = "願望、願う"

        # --- 結果を session に保存して GET にリダイレクト ---
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

# --- GET: 結果表示 ---
@app.route("/writing_result")
def writing_result():
    result = session.get('writing_result')  # pop ではなく get に変更
    if not result:
        flash("表示する結果がありません。")
        logger.warning("writing_result not found in session")
        return redirect(url_for("writing_quiz"))

    logger.info("writing_result retrieved from session: %s", result)

    return render_template(
        "writing_result.html",
        **result
    )

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
