import os
from flask import Flask

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_secret_for_local_only")  # ローカルはデフォルトでOK
