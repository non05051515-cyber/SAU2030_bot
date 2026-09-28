FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY telegram_payments.py customer_inbox.py required_group.py channel_catalog.py bot.py catalog.json storefront.py product_options.py discounts.py payment_methods.py imported_catalog.json iptv_extension.py broadcast_admin.py chatgpt_extension.py welcome_editor.py runner.py ./
COPY assets/ ./assets/
CMD ["python", "runner.py"]
