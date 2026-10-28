FROM python:3.12-slim-bookworm@sha256:62ed8e07e544e8555c5d93748d5275015136b7f5c52b5e8679b4de339c3779d3
RUN mkdir -p /state && chown 1000:1000 /state
WORKDIR /app
COPY watcher.py /app/watcher.py
USER 1000:1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "/app/watcher.py"]
CMD ["--loop"]
