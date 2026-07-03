FROM python:3.11-slim
# No COPY — the repo is mounted at /repo at runtime via docker run -v.
# No WORKDIR — each make target sets -w /repo explicitly.
RUN pip install --no-cache-dir anthropic httpx pytest pyyaml
