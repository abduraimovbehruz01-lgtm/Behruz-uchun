FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Render'da /tmp 2 GB bilan cheklangan — kinolar /var/data da saqlanadi
RUN mkdir -p /var/data/kino
ENV WORK_DIR=/var/data/kino
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && (pip install cryptg || echo "cryptg o'rnatilmadi - katta fayllar biroz sekinroq yuklanadi")

COPY *.py ./

CMD ["python", "bot.py"]
