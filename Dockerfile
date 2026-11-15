FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Copy all runtime Python modules; never copy development data or secrets.
COPY app.py backup.py offsite_backup.py backup_job.py restore.py scheduler.py preflight.py deployment_gate.py live_probe.py release_check.py instagram_oauth.py instagram_publish.py publish_runner.py token_vault.py ./
COPY static ./static
EXPOSE 10000
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
