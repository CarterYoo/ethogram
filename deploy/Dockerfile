# Built from a bundle made by scripts/bundle.sh (app/, data/, serve.sh). Serving needs only the standard library.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 SWARMGRAPH_SHARE=1
WORKDIR /srv
COPY app/ app/
COPY data/ data/
COPY serve.sh .
RUN useradd --create-home sg && chown -R sg /srv
USER sg
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/healthz', timeout=4)"
CMD ["sh", "serve.sh"]

