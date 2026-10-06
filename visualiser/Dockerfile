FROM python:3.12-slim

# Standard library only: nothing to install.
WORKDIR /app
COPY server.py ./
COPY vis ./vis
COPY static ./static
COPY visualiser.docker.json ./visualiser.json

RUN useradd --system --uid 10001 visualiser
USER visualiser

ENV VIS_CONFIG=/app/visualiser.json PYTHONUNBUFFERED=1
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=2).status == 200 else 1)"
CMD ["python", "server.py"]
