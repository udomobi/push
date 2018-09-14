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
from functools import reduce
from django_redis import get_redis_connection

from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.msgs.models import Msg

logger = logging.getLogger(__name__)


class EmailBodyParse:
    REGEX_GMAIL = r'(.*)((On|Em)(.*)(wrote|escreveu):)'
    REGEX_OUTLOOK = r'(.*)(________________________________)'
    REGEX_OUTLOOK_2 = r'(.*)(------------------------------)'
    REGEX_ZOHO = r'(.*)(---- (On|Em)(.*)(wrote|escreveu) ----)'
    SIG_REGEX = r'(.*)((--|__|-\w)|(^Sent from my (\w+\s*){1,3}))'

    REGEX_LIST = [
        REGEX_GMAIL,
        REGEX_OUTLOOK,
        REGEX_OUTLOOK_2,
        REGEX_ZOHO,
        SIG_REGEX,
    ]

    def __init__(self, text):
        self.text = text

    def get_body(self):
        def reduce_fn(current, REGEX):
            match = re.search(REGEX, self.text, re.DOTALL | re.MULTILINE)
            if match:
                result = match.group(1)
                if len(result) < len(current):
                    return result
            return current
        return reduce(reduce_fn, self.REGEX_LIST, self.text)

    @property
    def body(self):
        return self.get_body().strip()


@task(track_started=True, name='check_channel_mailbox')
def check_channel_mailbox():
    r = get_redis_connection()
    for channel in Channel.objects.filter(is_active=True, channel_type='EM'):
        pop_hostname = channel.config['EMAIL_POP_HOSTNAME']

        if pop_hostname:
            key = 'check_channel_mailbox_%d' % channel.id

            try:
                server = POP3_SSL(pop_hostname, channel.config['EMAIL_POP_PORT'])
                server.user(channel.config['EMAIL_USERNAME'])
                server.pass_(channel.config['EMAIL_PASSWORD'])

                logger.info(server.getwelcome())
                logger.info(server.stat())

                num_messages = len(server.list()[1])
                emails_list = r.get(key)

                if emails_list is None:
                    emails_list = []
                    r.set(key, emails_list)

                for i in range(num_messages):
                    raw_email = b"\n".join(server.retr(i + 1)[1])
                    parsed_email = email.message_from_string(raw_email)
                    payload = parsed_email.get_payload()

                    message_payload = StringIO.StringIO(raw_email)
                    message = rfc822.Message(message_payload)

                    if isinstance(payload, list) and message['Message-ID'] not in emails_list:
                        emails_list.append(message['Message-ID'])
                        match_sender = re.search(r'[\w\.-]+@[\w\.-]+', message['From'])

                        if match_sender:
                            sender = match_sender.group(0)
                            content = payload[0].get_payload()

                            if isinstance(content, str):
                                encoding = payload[0].get('Content-Transfer-Encoding')

                                if encoding:
                                    content = content.decode(encoding)

                                body = EmailBodyParse(content).get_body()
                                urn = URN.from_parts(channel.schemes[0], sender)
                                sms = Msg.create_incoming(channel, urn, body, date=parse(message['Date']))

                                logger.info('New Email: {}'.format(body.decode('utf8')))
                                logger.info('SMS Accepted: {}'.format(sms.id))
                r.set(key, emails_list)
                server.quit()
            except Exception as e:
                logger.error(e)
