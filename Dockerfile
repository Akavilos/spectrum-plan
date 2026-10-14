ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY bot.py run.py panel.html ./
COPY role_system ./role_system
COPY tests ./tests
COPY LICENSE ./
COPY docs/legal ./docs/legal
CMD ["python", "run.py"]
