FROM python:3.11-slim AS service

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

ARG UID=1000
ARG GID=1000
RUN groupadd --gid ${GID} appuser && \
    useradd --uid ${UID} --gid ${GID} --create-home appuser

USER appuser
WORKDIR /home/appuser/app

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/home/appuser/.local/bin:$PATH"

COPY --chown=appuser:appuser requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY --chown=appuser:appuser . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]