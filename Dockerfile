FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY bot.py catalog.json storefront.py imported_catalog.json iptv_extension.py broadcast_admin.py chatgpt_extension.py runner.py ./
COPY assets/ ./assets/
CMD ["python", "runner.py"]
