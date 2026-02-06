FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir mwparserfromhell brotli

COPY process_data.py /app/
COPY index.html sw.js version.json app.webmanifest /app/www/
COPY favicon.ico favicon-48.png favicon-256.png /app/www/
COPY smoldata.json.br /app/www/
RUN python -c "import brotli; open('/app/www/smoldata.json','wb').write(brotli.decompress(open('/app/www/smoldata.json.br','rb').read()))" && \
    rm /app/www/smoldata.json.br

EXPOSE 80

CMD ["python", "-m", "http.server", "80", "--directory", "/app/www"]
