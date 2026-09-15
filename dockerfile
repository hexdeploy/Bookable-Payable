FROM python:3.11-slim

# OCR system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-deu \
    tesseract-ocr-est \
    tesseract-ocr-fra \
    tesseract-ocr-por \
    tesseract-ocr-msa \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p output

CMD ["python", "pipeline.py", "documents/"]