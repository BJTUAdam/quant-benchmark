FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --only-binary=:all: --index-url "${PIP_INDEX_URL}" \
    numpy==1.26.4 pandas==2.2.3 scipy==1.11.4

# docker run supplies the current WSL user's numeric uid/gid. The image itself
# contains no task package, API key, project checkout, or Docker socket.
WORKDIR /workspace
