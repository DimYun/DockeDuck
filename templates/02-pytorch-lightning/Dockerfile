# Inherit from template 01-base-cuda
FROM dockduck-base:latest AS trainer

# dev tools only in this stage
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
USER appuser

COPY --chown=appuser:appuser requirements.txt .
RUN if command -v uv > /dev/null; then \
        uv pip install --system -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi

# ClearML agent config via env
ENV CLEARML_WEB_HOST=""
ENV CLEARML_API_HOST=""
ENV CLEARML_FILES_HOST=""
ENV CLEARML_API_ACCESS_KEY=""
ENV CLEARML_API_SECRET_KEY=""

COPY --chown=appuser:appuser . .
CMD ["python3", "src/train.py"]