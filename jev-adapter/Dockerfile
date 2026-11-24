FROM python:3.11-slim
WORKDIR /app
COPY catalog.py server.py /app/
EXPOSE 8080
CMD ["python", "-u", "/app/server.py"]
