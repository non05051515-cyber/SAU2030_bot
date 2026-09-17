FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY bot.py catalog.json storefront.py imported_catalog.json iptv_extension.py ./
COPY assets/ ./assets/
CMD ["python", "-c", "import iptv_extension; import runpy; runpy.run_path('bot.py', run_name='__main__')"]
