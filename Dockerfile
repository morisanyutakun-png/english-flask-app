# ベースイメージ
FROM python:3.11-slim

# 作業ディレクトリ
WORKDIR /app

# 依存関係コピー＆インストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリコードコピー
COPY . .

# 環境変数（Cloud Run で上書き可能）
ENV PORT 8080
ENV FLASK_ENV=production

# Cloud Run 起動コマンド
CMD ["python", "app.py"]
