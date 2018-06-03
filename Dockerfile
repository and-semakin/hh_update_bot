FROM python:3.6.5-slim-stretch
RUN mkdir /app
WORKDIR /app
RUN apt-get update && apt-get install -y gcc
ADD requirements.txt requirements.txt
RUN pip install -r requirements.txt