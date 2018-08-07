#!/bin/bash

gunicorn temba.wsgi --log-config gunicorn-logging.conf -c gunicorn.conf.py
