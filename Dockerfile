# Minimal image for the Glofox -> Meta CAPI sync. Stdlib only, no dependencies.
FROM python:3.12-slim
WORKDIR /app
COPY glofox_to_meta_capi.py /app/
# Cloud Run Jobs runs this once per execution (the scheduler triggers each run).
ENTRYPOINT ["python", "/app/glofox_to_meta_capi.py"]
