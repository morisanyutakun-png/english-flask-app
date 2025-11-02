from flask import Flask, request, render_template_string, session, redirect, url_for
import sqlite3
from datetime import datetime
from google import genai
import json
import re

# ====== Gemini 設定 ======
API_KEY = "AIzaSyD_HCSYj7tLGlRbS28UIQIIFql2AplWzDs"
client = genai.Client(api_key=API_KEY, vertexai=False)

app = Flask(__name__)
app.secret_key = "supersecretkey"

DB_FILE = "english_learning.db"

# ====== DB初期化 ======
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT UNIQUE,
            definition_en TEXT,
            definition_ja TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            grade TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS student_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            word_id INTEGER,
            score INTEGER,
            feedback TEXT,
            example TEXT,
            attempt_date TEXT,
            FOREIGN KEY(student_id) REFERENCES students(id),
            FOREIGN KEY(word_id) REFERENCES words(id)
        )""")
        conn.commit()

init_db()

# ====== 単語をランダム取得 ======
def get_random_word():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT word, definition_en, definition_ja FROM words ORDER BY RANDOM() LIMIT 1")
        return cur.fetchone()

# ====== Geminiで採点 ======
def evaluate_with_gemini(word, correct_answer, user_answer):
    try:
        prompt = f"""
あなたは英単語学習の先生です。
学習者の回答を採点し、アドバイスと例文を日本語で作ってください。
必ずJSON形式のみで出力してください。余計な文字は付けないでください。

単語: {word}
正しい意味: {correct_answer}
学習者の回答: {user_answer}

出力形式:
{{
  "score": 0-100 の整数,
  "feedback": "学習者へのアドバイス",
  "example": "その単語を使った例文（英語＋日本語訳）"
}}
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text
        print("Gemini 生出力:", text)
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {"score": 0, "feedback": "採点できませんでした", "example": ""}
    except Exception as e:
        print("Gemini エラー:", e)
        return {"score": 0, "feedback": "採点できませんでした", "example": ""}

# ====== 平均スコア取得 ======
def get_average_score(name):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM students WHERE name=?", (name,))
        student = cur.fetchone()
        if not student:
            return None
        student_id = student[0]
        cur.execute("SELECT AVG(score) FROM student_answers WHERE student_id=?", (student_id,))
        avg = cur.fetchone()[0]
        return round(avg, 2) if avg is not None else None

# ====== ランキング取得 ======
def get_ranking():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT s.name, ROUND(AVG(a.score), 2) as avg_score
        FROM students s
        JOIN student_answers a ON s.id = a.student_id
        GROUP BY s.id
        ORDER BY avg_score DESC
        LIMIT 20
        """)
        return cur.fetchall()

# ====== クイズ画面 ======
@app.route("/", methods=["GET", "POST"])
def quiz():
    average_score = None

    if request.method == "POST":
        # セッションに名前を保存
        name = request.form["name"]
        session["name"] = name
        word = request.form["word"]
        answer = request.form["answer"]
        correct_meaning = request.form["correct"]

        gemini_result = evaluate_with_gemini(word, correct_meaning, answer)

        with sqlite3.connect(DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM students WHERE name=?", (name,))
            student = cur.fetchone()
            if not student:
                cur.execute("INSERT INTO students (name, grade) VALUES (?, ?)", (name, "不明"))
                conn.commit()
                cur.execute("SELECT id FROM students WHERE name=?", (name,))
                student = cur.fetchone()
            student_id = student[0]

            cur.execute("SELECT id FROM words WHERE word=?", (word,))
            w = cur.fetchone()
            if w:
                cur.execute("""
                    INSERT INTO student_answers (student_id, word_id, score, feedback, example, attempt_date)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    student_id, w[0],
                    gemini_result.get("score", 0),
                    gemini_result.get("feedback", ""),
                    gemini_result.get("example", ""),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ))
                conn.commit()

        # 平均スコア取得
        average_score = get_average_score(name)
        html = f"""
        <h2>Geminiフィードバック</h2>
        <p>スコア: {gemini_result.get('score')}</p>
        <p>アドバイス: {gemini_result.get('feedback')}</p>
        <p>例文: {gemini_result.get('example')}</p>
        """
        if average_score is not None:
            html += f"<p>これまでの平均スコア: {average_score}</p>"
        html += '<a href="/">次の問題へ</a>'
        html += '<br><a href="/ranking">ランキングを見る</a>'
        html += '<br><a href="/logout">ログアウト</a>'
        return html

    # GET時はセッションから名前を取得
    name = session.get("name", "")
    word_data = get_random_word()
    if not word_data:
        return "DBに単語がありません。"
    word, def_en, def_ja = word_data
    html = f"""
    <h2>英単語クイズ</h2>
    <form method="post">
        名前: <input name="name" value="{name}" required><br>
        単語: <input name="word" value="{word}" readonly><br>
        意味（日本語）を入力: <input name="answer" required><br>
        <input type="hidden" name="correct" value="{def_ja}">
        <input type="submit" value="送信">
    </form>
    """
    if name:
        average_score = get_average_score(name)
        if average_score is not None:
            html += f"<p>平均スコア: {average_score}</p>"
        html += '<a href="/ranking">ランキングを見る</a>'
        html += '<br><a href="/logout">ログアウト</a>'

    return render_template_string(html)

# ====== ランキングページ ======
@app.route("/ranking")
def ranking():
    ranking_data = get_ranking()
    name = session.get("name", "")
    html = "<h2>ランキング（平均スコア順）</h2><ol>"
    for student_name, avg_score in ranking_data:
        if student_name == name:
            html += f"<li><b>{student_name}: {avg_score}</b></li>"
        else:
            html += f"<li>{student_name}: {avg_score}</li>"
    html += "</ol>"
    html += '<a href="/">クイズに戻る</a>'
    html += '<br><a href="/logout">ログアウト</a>'
    return render_template_string(html)

# ====== ログアウト ======
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("quiz"))

if __name__ == "__main__":
    app.run(debug=True, port=5001)
