import sqlite3
DB_FILE = "/tmp/english_learning.db"

with sqlite3.connect(DB_FILE) as conn:
    c = conn.cursor()
    c.execute("SELECT * FROM users;")
    users = c.fetchall()
    print("users table:", users)

    c.execute("SELECT * FROM student_answers;")
    answers = c.fetchall()
    print("student_answers table:", answers)
