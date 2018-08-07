FROM ilha/rapidpro-india:base

COPY . .
COPY settings.py.pre ${WEBAPP_HOME}/temba/settings.py

RUN apt-get install -y postgresql-client-9.3
RUN pip install -r requirements.txt --upgrade
RUN npm install -g coffeescript less && npm install

RUN python manage.py collectstatic --noinput
COPY settings.py.static ${WEBAPP_HOME}/temba/settings.py

EXPOSE 8000

ENTRYPOINT gunicorn temba.wsgi --log-config ${WEBAPP_HOME}/gunicorn-logging.conf -c ${WEBAPP_HOME}/gunicorn.conf.py
