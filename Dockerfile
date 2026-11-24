FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6
ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BLINDSPOT_DATA=/var/lib/blindspot
WORKDIR /app
COPY requirements.lock .
RUN python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock
RUN mkdir -p /var/lib/blindspot && chown 10001:10001 /var/lib/blindspot
COPY src/ src/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "blindspot.api:app", "--host", "0.0.0.0", "--port", "8000"]
