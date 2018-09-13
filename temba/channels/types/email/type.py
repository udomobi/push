# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import urllib
import time

from smtplib import SMTP

from email import encoders
from email.mime.image import MIMEImage
from email.mime.audio import MIMEAudio
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from django.utils.translation import ugettext_lazy as _

from temba.msgs.models import Attachment, WIRED
from temba.orgs.models import Org
from temba.contacts.models import Contact, EMAIL_SCHEME
from temba.utils.http import HttpEvent

from .views import ClaimView
from ...models import ChannelType, Channel


class EmailType(ChannelType):
    """
    A Email Channel Type
    """
    code = 'EM'
    category = ChannelType.Category.API

    name = "Email"
    icon = 'icon-envelop'
    show_config_page = False

    claim_blurb = _("""Send and receive messages by email""")
    claim_view = ClaimView

    schemes = [EMAIL_SCHEME]
    attachment_support = True
    free_sending = True

    def send(self, channel, msg, text):  # pragma: no cover
        smtp_hostname = channel.config['EMAIL_SMTP_HOSTNAME']

        if smtp_hostname:
            transport = SMTP
            start = time.time()

            org_obj = Org.objects.get(id=channel.org)
            contact_obj = Contact.objects.get(id=msg.contact)

            message = MIMEMultipart('alternative')
            message['Subject'] = channel.config['EMAIL_SUBJECT']
            message['From'] = '{0} <{1}>'.format(org_obj.name, channel.config['EMAIL_FROM'])
            message['To'] = '{0} <{1}>'.format(contact_obj.name, msg.urn_path)

            part = MIMEText(text.encode('utf8'), 'html')
            message.attach(part)

            attachments = Attachment.parse_all(msg.attachments)
            attachment = attachments[0] if attachments else None

            if attachment:
                mimefile = None
                category = attachment.content_type.split('/')[0]
                filename = attachment.url.split('/')[-1]

                webf = urllib.urlopen(attachment.url)
                content = webf.read()

                if category == 'image':
                    mimefile = MIMEImage(content)
                elif category == 'audio':
                    mimefile = MIMEAudio(content)
                else:
                    filename = attachment.url.split('/')[-1]
                    mimefile = MIMEBase('application', 'octet-stream')
                    mimefile.set_payload(content)
                    encoders.encode_base64(mimefile)

                if mimefile:
                    mimefile.add_header('Content-Disposition', 'attachment', filename=filename)
                    message.attach(mimefile)

            server = transport(smtp_hostname, channel.config['EMAIL_SMTP_PORT'])
            if channel.config['EMAIL_USE_TLS']:
                server.starttls()

            server.login(channel.config['EMAIL_USERNAME'], channel.config['EMAIL_PASSWORD'])
            server.sendmail(channel.config['EMAIL_FROM'], msg.urn_path, message.as_string())
            server.quit()

            event = HttpEvent('POST', '')
            event.status_code = 200
            event.response_body = msg.text

            Channel.success(channel, msg, WIRED, start, event=event)
