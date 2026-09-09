FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir . -c requirements.lock && useradd --create-home appuser && mkdir -p /app/var && chown appuser:appuser /app/var
USER appuser
EXPOSE 8000
CMD ["mediabias", "serve"]
