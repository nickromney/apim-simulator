FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN addgroup --system app && adduser --system --ingroup app --home /app app

WORKDIR /app

RUN pip install --no-cache-dir "fastapi[standard]>=0.115.0" "uvicorn[standard]>=0.35.0"

COPY --chown=app:app main.py ./main.py

EXPOSE 8000

USER app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
