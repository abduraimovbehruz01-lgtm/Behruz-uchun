FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# DejaVu — zaxira shrift (asosiysi fonts/ ichida)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && (apt-get install -y --no-install-recommends libglib2.0-0t64 || apt-get install -y --no-install-recommends libglib2.0-0 || true) \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && python -c "import cv2, numpy, PIL, pypdfium2, aiogram, google.cloud.firestore; print('cv2', cv2.__version__)"
COPY fonts ./fonts
COPY webapp ./webapp
COPY *.py ./
CMD ["python", "bot.py"]
