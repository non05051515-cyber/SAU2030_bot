FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY bot.py catalog.json storefront.py imported_catalog.json ./
COPY assets/ ./assets/
CMD ["python", "bot.py"]
