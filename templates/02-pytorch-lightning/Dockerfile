FROM base-python:latest AS trainer

# dev tools only in this stage
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
USER appuser

COPY --chown=appuser:appuser requirements.txt .
RUN uv pip install -r requirements.txt   # includes torch, lightning, clearml

# ClearML agent config via env — never bake credentials into image
ENV CLEARML_WEB_HOST=""
ENV CLEARML_API_HOST=""
ENV CLEARML_FILES_HOST=""
ENV CLEARML_API_ACCESS_KEY=""
ENV CLEARML_API_SECRET_KEY=""

COPY --chown=appuser:appuser . .
CMD ["python3", "train.py"]