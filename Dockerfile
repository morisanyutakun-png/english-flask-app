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
ENV FLASK_ENV production

# Cloud Run 用コマンド
# --timeout 300 で最大 5 分に設定（必要に応じて調整）
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "300", "app:app"]
