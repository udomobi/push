FROM ilha/rapidpro-india:base

RUN echo "Starting build"

#RapidPro setup
RUN mkdir /udo-rapidpro
WORKDIR /udo-rapidpro
RUN virtualenv env
RUN . env/bin/activate

ADD pip-freeze.txt /udo-rapidpro/pip-freeze.txt
RUN pip install -r pip-freeze.txt
RUN pip install uwsgi
ADD . /udo-rapidpro
COPY settings.py.pre /udo-rapidpro/temba/settings.py

RUN npm install -g coffeescript less && npm install && python manage.py collectstatic --noinput
RUN touch `echo $RANDOM`.txt

RUN python manage.py compress --extension=.haml

#Nginx setup
RUN echo "daemon off;" >> /etc/nginx/nginx.conf
RUN rm /etc/nginx/sites-enabled/default
RUN ln -s /udo-rapidpro/nginx.conf /etc/nginx/sites-enabled/

RUN rm /udo-rapidpro/temba/settings.pyc

COPY settings.py.static /udo-rapidpro/temba/settings.py

EXPOSE 8000
EXPOSE 80

#COPY docker-entrypoint.sh /udo-rapidpro/
RUN /usr/local/bin/pip install -U requests[security]

ENTRYPOINT ["/udo-rapidpro/entrypoint.sh"]

CMD ["supervisor"]

