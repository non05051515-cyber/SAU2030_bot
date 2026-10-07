FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY product_details.py product_announcements.py pandora_admin.py test_payment_execution.py payment_execution.py payment_diagnostics.py pandora_catalog_sync.py pandora_stock_notifications.py telegram_payments.py customer_inbox.py required_group.py channel_catalog.py bot.py catalog.json storefront.py product_options.py discounts.py payment_methods.py imported_catalog.json iptv_extension.py broadcast_admin.py chatgpt_extension.py welcome_editor.py local_delivery.py runner.py ./
COPY assets/ ./assets/
RUN python -m unittest -v test_payment_execution
CMD ["python", "runner.py"]

