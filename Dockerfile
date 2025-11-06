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

# Cloud Run 用に Gunicorn で起動
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
