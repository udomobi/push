# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import email
import StringIO
import rfc822
import re
import json
import six

from celery.task import task
from poplib import POP3, POP3_SSL
from dateutil.parser import parse
from django_redis import get_redis_connection
from email_reply_parser import EmailMessage
from email.message import Message

from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.msgs.models import Msg
from temba.utils.dates import datetime_to_ms

logger = logging.getLogger(__name__)


class EmaiBodyParser(EmailMessage):
    SIG_REGEX = re.compile(r'(--|__|-\w)|(^(Sent from my|Enviado do meu) (\w+\s*){1,3})')
    QUOTE_HDR_REGEX = re.compile('(On|Em).*(wrote|escreveu):$')
    QUOTED_REGEX = re.compile(r'(>+)')
    HEADER_REGEX = re.compile(r'^(From|Sent|To|Subject|De|Enviado|Para|Assunto): .+')

    _MULTI_QUOTE_HDR_REGEX = r'(?!(On|Em).*(On|Em)\s.+?(wrote|escreveu):)((On|Em)\s(.+?)(wrote|escreveu):)'
    MULTI_QUOTE_HDR_REGEX = re.compile(_MULTI_QUOTE_HDR_REGEX, re.DOTALL | re.MULTILINE)
    MULTI_QUOTE_HDR_REGEX_MULTILINE = re.compile(_MULTI_QUOTE_HDR_REGEX, re.DOTALL)


class EmailPop3Transport():
    def __init__(self, hostname, port=None, ssl=False):
        self.hostname = hostname
        self.port = port

        if ssl:
            self.transport = POP3_SSL
            if not self.port:
                self.port = 995
        else:
            self.transport = POP3
            if not self.port:
                self.port = 110

    def connect(self, username, password):
        self.server = self.transport(self.hostname, self.port)
        self.server.user(username)
        self.server.pass_(password)

        logger.info(self.server.getwelcome())
        logger.info(self.server.stat())

    def get_message_body(self, message_lines):
        return '\r\n'.join(message_lines)

    def get_messages(self, condition=None):
        message_count = len(self.server.list()[1])
        for i in range(message_count):
            try:
                msg_contents = self.get_message_body(self.server.retr(i + 1)[1])
                message = email.message_from_string(msg_contents)

                if condition and not condition(message):
                    continue

                yield message
            except email.errors.MessageParseError:
                continue
        self.server.quit()
        return

    def convert_header_to_unicode(self, header):
        default_charset = 'iso8859-1'

        if six.PY2 and isinstance(header, six.text_type):
            return header

        def _decode(value, encoding):
            if isinstance(value, six.text_type):
                return value
            if not encoding or encoding == 'unknown-8bit':
                encoding = default_charset
            return value.decode(encoding, 'replace')

        try:
            return ''.join(
                [
                    (
                        _decode(bytestr, encoding)
                    ) for bytestr, encoding in email.header.decode_header(header)
                ]
            )
        except UnicodeDecodeError:
            return header.decode(default_charset, 'replace')

    def get_body_from_message(self, message, maintype, subtype):
        body = six.text_type('')
        for part in message.walk():
            if part.get('content-disposition', '').startswith('attachment;'):
                continue

            if part.get_content_maintype() == maintype and \
                    part.get_content_subtype() == subtype:
                charset = part.get_content_charset()
                this_part = part.get_payload(decode=True)
                if charset:
                    try:
                        this_part = this_part.decode(charset, 'replace')
                    except LookupError:
                        this_part = this_part.decode('ascii', 'replace')
                    except ValueError:
                        this_part = this_part.decode('ascii', 'replace')
                else:
                    this_part = this_part.decode('ascii', 'replace')

                body += this_part
        return body

    def get_email_object(self, body):
        return email.message_from_string(body)

    def get_text(self, body):
        return self.get_body_from_message(
            self.get_email_object(body), 'text', 'plain'
        ).replace('=\n', '').strip()

    def get_html(self, body):
        return self.get_body_from_message(
            self.get_email_object(body), 'text', 'html'
        ).replace('=\n', '').strip()

    def get_dehydrated_message(self, msg, record):
        new = Message()

        if msg.is_multipart():
            for header, value in msg.items():
                new[header] = value

            for part in msg.get_payload():
                new.attach(self.get_dehydrated_message(part, record))
        else:
            content_charset = msg.get_content_charset()
            if not content_charset:
                content_charset = 'ascii'
            try:
                msg.get_payload(decode=True).decode(content_charset)
            except LookupError:
                msg.set_payload(
                    msg.get_payload(decode=True).decode(
                        'ascii',
                        'ignore'
                    )
                )
            except ValueError:
                msg.set_payload(
                    msg.get_payload(decode=True).decode(
                        'ascii',
                        'ignore'
                    )
                )
            new = msg
        return new

    def process_incoming_message(self, message):
        msg = dict()
        if 'Subject' in message:
            msg['subject'] = (
                self.convert_header_to_unicode(message.get('Subject'))[0:255]
            )

        if 'Message-Id' in message:
            msg['message_id'] = message.get('Message-Id')[0:255].strip()

        if 'Date' in message:
            msg['date'] = message.get('Date')[0:255].strip()

        if 'From' in message:
            msg['from_header'] = self.convert_header_to_unicode(message.get('From'))

        if 'To' in message:
            msg['to_header'] = self.convert_header_to_unicode(message.get('To'))

        elif 'Delivered-To' in message:
            msg['to_header'] = self.convert_header_to_unicode(
                message.get('Delivered-To')
            )

        message = self.get_dehydrated_message(message, msg)

        try:
            body = message.as_string()
        except KeyError as exc:
            return None

        body_parser = EmaiBodyParser(self.get_text(body)).read().reply
        if not body_parser:
            body_parser = self.get_html(body)
        
        msg['body'] = body_parser
        return msg


@task(track_started=True, name='check_channel_mailbox')
def check_channel_mailbox():
    r = get_redis_connection()

    if r.get('channel_mailbox_running'):  # pragma: no cover
        return

    with r.lock('channel_mailbox_running', 3600):
        for channel in Channel.objects.filter(is_active=True, channel_type='EM'):
            pop_hostname = channel.config['EMAIL_POP_HOSTNAME']
            channel_created_on = datetime_to_ms(channel.created_on)

            if pop_hostname:
                key = 'check_channel_mailbox_%d' % channel.id
                emails_list = r.get(key)

                if emails_list:
                    emails_list = json.loads(emails_list)
                else:
                    emails_list = {}
                    r.set(key, emails_list)

                try:
                    transport = EmailPop3Transport(pop_hostname, channel.config['EMAIL_POP_PORT'], channel.config['EMAIL_USE_SSL'])
                    transport.connect(channel.config['EMAIL_USERNAME'], channel.config['EMAIL_PASSWORD'])

                    for m in transport.get_messages():
                        message = transport.process_incoming_message(m)
                        email_created_on = datetime_to_ms(parse(message['date']))

                        if message['message_id'] not in emails_list and email_created_on > channel_created_on:
                            emails_list[message['message_id']] = message['message_id']
                            match_sender = re.search(r'[\w\.-]+@[\w\.-]+', message['from_header'])

                            if match_sender:
                                sender = match_sender.group(0)

                                urn = URN.from_parts(channel.schemes[0], sender)
                                sms = Msg.create_incoming(channel, urn, message['body'], date=parse(message['date']), external_id=message['message_id'])
                                sms.metadata = {'subject': message['subject']}
                                sms.save()

                                logger.info('New E-mail: {}'.format(message['body']))
                                logger.info('SMS Accepted: {}'.format(sms.id))

                    r.set(key, json.dumps(emails_list))
                except Exception as e:
                    logger.error(e)
