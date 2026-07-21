FROM python:3.12-slim

WORKDIR /app

# ffmpeg is used to grab a single live frame for the home page preview
# thumbnail. It is the only system dependency the gate needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY routes ./routes

# Bake the country database into the image (used for the US-only lock).
RUN python fetch_geodb.py || true

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
