FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Isolated Instagram connection review release; preserve the original 5.8 staging archive.
COPY SocialPlan_5_8_Instagram_Baglanti_Duzeltmesi_Temiz.zip /tmp/socialplan-release.zip
RUN python -m zipfile -e /tmp/socialplan-release.zip /app \
    && rm /tmp/socialplan-release.zip \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m py_compile app.py
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
