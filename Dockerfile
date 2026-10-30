# Tag epingle : une mise a jour doit arriver le jour ou on l'a decidee.
FROM python:3.11-slim

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin gk \
 && mkdir -p /var/lib/gk /etc/gk \
 && chown -R gk:gk /var/lib/gk /etc/gk

WORKDIR /app
COPY pyproject.toml ./
COPY server/ ./server/
RUN pip install --no-cache-dir .

COPY motifs.yaml /etc/gk/motifs.yaml
COPY optimiseur.yaml /etc/gk/optimiseur.yaml
COPY lexique.yaml /etc/gk/lexique.yaml
COPY config.example.yaml /etc/gk/config.yaml

USER gk
ENV GK_CONFIG=/etc/gk/config.yaml \
    PYTHONUNBUFFERED=1
EXPOSE 8765

# Le healthcheck interroge /health, qui execute une vraie requete : un GK vivant
# mais casse doit se voir.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status==200 else 1)"

CMD ["python3", "-m", "gk.server"]
