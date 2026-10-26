FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /repo
COPY backend/requirements*.txt backend/
RUN pip install --no-cache-dir -r backend/requirements-dev.txt
COPY . .
ENV PYTHONPATH=/repo/backend
ENTRYPOINT ["python", "scripts/run-tests.py"]
