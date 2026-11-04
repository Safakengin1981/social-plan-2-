FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Staging build: unpack the exact tested SocialPlan 5.8 release archive.
# Build intentionally fails if the correct archive is missing.
COPY SocialPlan_5_8_Yeniden_Baslatma_Testi.zip.zip /tmp/socialplan-release.zip
RUN python -m zipfile -e /tmp/socialplan-release.zip /app \
    && rm /tmp/socialplan-release.zip \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m py_compile app.py
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
