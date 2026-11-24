FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV KEEL_PUBLIC=1 KEEL_HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 6870
CMD ["python", "run.py"]
