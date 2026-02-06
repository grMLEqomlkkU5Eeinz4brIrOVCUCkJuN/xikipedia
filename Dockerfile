FROM python:3.12-slim

RUN apt-get update && apt-get install -y nginx && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir mwparserfromhell

COPY nginx.conf /etc/nginx/nginx.conf
COPY process_data.py /app/

COPY index.html sw.js version.json app.webmanifest /app/www/
COPY favicon.ico favicon-48.png favicon-256.png /app/www/
COPY smoldata.json.gz /app/www/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
