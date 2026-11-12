# Python 3.12 with Bullseye also supports the existing older Docker runtime.
ARG PYTHON_IMAGE=python:3.12-slim-bullseye
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY gateway/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt --index-url "${PIP_INDEX_URL}"
COPY gateway/ ./gateway/
COPY service/ ./service/
LABEL com.material-checklist.component="dify-wrapper-and-rule-reader"
EXPOSE 8000
CMD ["python", "-B", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
