FROM --platform=linux/amd64 python:3.12-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && \
    apt-get install -y gcc libheif-dev libffi-dev python3-dev libjpeg-dev libpng-dev && \
    rm -rf /var/lib/apt/lists/*


COPY requirements.txt .
RUN pip install --use-pep517 --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]