FROM ubuntu:22.04

ARG USERNAME="appuser"
ARG UID="1000"
ARG GID="1000"

ENV DEBIAN_FRONTEND=noninteractive \
    OPT_DIR="/opt/dockduck" \
    VENV_DIR="/opt/dockduck/python_venv/initial_venv" \
    WORK_DIR="/home/appuser/project"

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    curl git make build-essential python3-venv python3-dev && \
    apt-get clean

RUN groupadd --gid ${GID} ${USERNAME} && \
    useradd -l -m -s /bin/bash --uid ${UID} --gid ${GID} ${USERNAME} && \
    mkdir -p ${VENV_DIR} ${WORK_DIR} && \
    chown -R ${USERNAME}:${GID} ${OPT_DIR} ${WORK_DIR}

USER ${USERNAME}
WORKDIR ${WORK_DIR}

RUN python3 -m venv ${VENV_DIR}
ENV PATH="${VENV_DIR}/bin:${PATH}"

COPY --chown=${USERNAME}:${GID} requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=${USERNAME}:${GID} . ${WORK_DIR}/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]