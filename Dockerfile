FROM ilha/rapidpro-base:base

COPY pip-freeze.txt .

RUN pip install -r pip-freeze.txt

COPY package.json .

RUN npm install

COPY . .

COPY settings.py.pre temba/settings.py

RUN python manage.py collectstatic --noinput
RUN python manage.py compress --extension=.haml,.html

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]

CMD ["supervisor"]
