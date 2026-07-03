FROM ubuntu:22.04

ARG USERNAME="appuser"
ARG UID="1000"
ARG GID="1000"

ENV DEBIAN_FRONTEND=noninteractive \
    VENV_DIR="/opt/dockeduck/venv" \
    WORK_DIR="/home/appuser/app"

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    curl python3-venv python3-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid ${GID} ${USERNAME} 2>/dev/null || true && \
    useradd -l -m -s /bin/bash --uid ${UID} --gid ${GID} ${USERNAME} && \
    mkdir -p ${VENV_DIR} ${WORK_DIR} && \
    chown -R ${USERNAME}:${GID} /opt/dockeduck ${WORK_DIR}

USER ${USERNAME}
WORKDIR ${WORK_DIR}

RUN python3 -m venv ${VENV_DIR}
ENV PATH="${VENV_DIR}/bin:${PATH}"

COPY --chown=${USERNAME}:${GID} requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=${USERNAME}:${GID} src/ ./src/
COPY --chown=${USERNAME}:${GID} entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
