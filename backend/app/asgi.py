import os
from .main import create_app

os.environ.setdefault('OTEL_PYTHON_EXCLUDED_URLS', '.*/auth/.*')
os.environ.setdefault('OTEL_PYTHON_FASTAPI_EXCLUDED_URLS', '.*/auth/.*')

if os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING'):
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(sampling_ratio=0.1)
app = create_app()
