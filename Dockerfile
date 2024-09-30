FROM --platform=linux/amd64 python:3.12.4-alpine3.20

WORKDIR /app

RUN apk add --no-cache libheif-dev

COPY requirements.txt .
RUN pip install --use-pep517 --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]