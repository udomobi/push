FROM ilha/push:base

COPY . .

COPY settings.py.pre temba/settings.py

RUN python manage.py collectstatic --noinput
RUN python manage.py compress --extension=.haml

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
