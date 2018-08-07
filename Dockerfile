FROM ilha/rapidpro-india:base

COPY . .
COPY settings.py.static ${WEBAPP_HOME}/temba/settings.py
RUN npm install -g coffeescript less && npm install && python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT gunicorn temba.wsgi --log-config ${WEBAPP_HOME}/gunicorn-logging.conf -c ${WEBAPP_HOME}/gunicorn.conf.py
