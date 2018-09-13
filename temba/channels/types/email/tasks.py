# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import email
import StringIO
import rfc822
import re

from celery.task import task
from poplib import POP3_SSL
from dateutil.parser import parse

from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.msgs.models import Msg

logger = logging.getLogger(__name__)


@task(track_started=True, name='check_channel_mailbox')
def check_channel_mailbox():
    for channel in Channel.objects.filter(is_active=True, channel_type='EM'):
        pop_hostname = channel.config['EMAIL_POP_HOSTNAME']

        if pop_hostname:
            try:
                server = POP3_SSL(pop_hostname, channel.config['EMAIL_POP_PORT'])
                server.user(channel.config['EMAIL_USERNAME'])
                server.pass_(channel.config['EMAIL_PASSWORD'])

                logger.info(server.getwelcome())
                logger.info(server.stat())

                num_messages = len(server.list()[1])

                for i in range(num_messages):
                    raw_email = b"\n".join(server.retr(i + 1)[1])
                    parsed_email = email.message_from_string(raw_email)
                    payload = parsed_email.get_payload()
                    body = payload[0].get_payload()
                    body = body.split('\n')

                    message_payload = StringIO.StringIO(raw_email)
                    message = rfc822.Message(message_payload)

                    match = re.search(r'[\w\.-]+@[\w\.-]+', message['From'])
                    sender = match.group(0)

                    urn = URN.from_parts(channel.schemes[0], sender)
                    sms = Msg.create_incoming(channel, urn, body[0], date=parse(message['Date']))

                    logger.info('New Email: {}'.format(body))
                    logger.info('SMS Accepted: {}'.format(sms.id))
                    server.dele(i + 1)
                server.quit()
            except Exception as e:
                logger.error(e)
