FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN useradd -r -u 10001 lms && mkdir -p /app/data && chown -R lms:lms /app
USER lms
ENV LMS_HOST=0.0.0.0 LMS_PORT=8080 LMS_DB_PATH=/app/data/lms.db
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=2)"
CMD ["python","-m","lms.server"]
