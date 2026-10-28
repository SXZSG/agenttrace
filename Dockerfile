FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AGENTTRACE_MODE=mock AGENTTRACE_DB_PATH=/data/agenttrace.sqlite3
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir . && useradd --create-home --uid 10001 agenttrace && mkdir /data && chown agenttrace:agenttrace /data
USER agenttrace
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)"
CMD ["python", "-m", "uvicorn", "agenttrace.api:app", "--host", "0.0.0.0", "--port", "8001"]
