# ベースイメージ
FROM python:3.11-slim

# 作業ディレクトリ
WORKDIR /app

# 依存関係コピー＆インストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリコードコピー
COPY . .

# 環境変数
ENV PORT 8080

# Cloud Run 用コマンド（PORT 環境変数を Gunicorn に渡す）
CMD exec gunicorn --bind 0.0.0.0:${PORT} \
    --workers 1 --threads 8 --timeout 0 app:app
