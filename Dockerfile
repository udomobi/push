FROM ubuntu:trusty

#apt-get install

RUN apt-get update && apt-get install -qyy \
    -o APT::Install-Recommends=false -o APT::Install-Suggests=false \
    build-essential python-imaging git python-setuptools  ncurses-dev python-virtualenv  python-pip postgresql-client-9.3 libpq-dev \
    libpython-dev lib32ncurses5-dev pypy libffi6 openssl libgeos-dev \
    coffeescript node-less yui-compressor gcc libreadline6 libreadline6-dev patch libffi-dev libssl-dev libxml2-dev libxslt1-dev  python-dev \
    python-zmq libzmq-dev nginx libpcre3 libpcre3-dev supervisor wget libjpeg-dev libjpeg-turbo8-dev libmagic-dev
WORKDIR /tmp
RUN wget http://download.osgeo.org/gdal/1.11.0/gdal-1.11.0.tar.gz
RUN tar xvfz gdal-1.11.0.tar.gz
RUN cd gdal-1.11.0;./configure --with-python; make -j4; make install
RUN ldconfig
RUN rm -rf /tmp/* 

#RapidPro setup
RUN mkdir /udo-rapidpro
WORKDIR /udo-rapidpro
RUN virtualenv env
RUN . env/bin/activate
ADD pip-freeze.txt /udo-rapidpro/pip-freeze.txt
RUN pip install -r pip-freeze.txt
RUN pip install -U pip
RUN pip install requests[security] --upgrade
RUN pip install uwsgi
ADD . /udo-rapidpro
COPY settings.py.pre /udo-rapidpro/temba/settings.py

RUN apt-get install -y curl
RUN curl -sL https://deb.nodesource.com/setup_6.x | bash -
RUN apt-get install -y nodejs
RUN npm install -g bower
RUN npm install -g less
RUN npm install -g coffee-script
RUN bower install --allow-root
RUN python manage.py collectstatic --noinput

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

ENTRYPOINT ["/udo-rapidpro/entrypoint.sh"]

CMD ["supervisor"]

#Image cleanup
RUN apt-get clean
RUN rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*[~]$ 

