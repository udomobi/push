FROM ilha/rapidpro-india:base

COPY . .
RUN npm install -g coffeescript less && npm install && python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT gunicorn temba.wsgi --log-config ${WEBAPP_HOME}/gunicorn-logging.conf -c ${WEBAPP_HOME}/gunicorn.conf.py
