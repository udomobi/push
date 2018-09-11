# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from smtplib import SMTP, SMTP_SSL

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import EMAIL_SCHEME
from temba.orgs.models import Org
from temba.contacts.models import Contact

from .views import ClaimView
from ...models import ChannelType


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
    attachment_support = False
    free_sending = True

    def send(self, channel, msg, text):  # pragma: no cover
        # print(channel)
        # print(msg.org.name)
        # print(msg.urn_path)
        print(text)

        '''
        sender = 'edu.douglas@ilhasoft.com.br'
        receivers = ['edu.douglas@ilhasoft.com.br']

        message = """From: From Person <edu.douglas@ilhasoft.com.br>
        To: To Person <edu.douglas@ilhasoft.com.br>
        Subject: SMTP e-mail test

        This is a test e-mail message.
        """

        smtpObj = smtplib.SMTP_SSL('smtp.gmail.com')
        smtpObj.login('edu.douglas@ilhasoft.com.br', 'x9*rxic9*fb')
        smtpObj.sendmail(sender, receivers, message)
        print("Successfully sent email")
        '''

        smtp_hostname = channel.config['EMAIL_SMTP_HOSTNAME']

        if smtp_hostname:
            transport = SMTP
            if channel.config['EMAIL_USE_SSL']:
                transport = SMTP_SSL

            org_obj = Org.objects.get(id=channel.org)
            contact_obj = Contact.objects.get(id=msg.contact)

            # message = """From: {0} <{1}>\n
            # To: {2} <{3}>\n
            # Subject: SMTP e-mail test\n
            # {3}\n
            # """.format(
            #     org_obj.name,
            #     channel.config['EMAIL_FROM'],
            #     contact_obj.name,
            #     msg.urn_path,
            #     text)

            # print(message)

            message = MIMEMultipart('alternative')
            message['Subject'] = "Link"
            message['From'] = '{0} <{1}>'.format(org_obj.name, channel.config['EMAIL_FROM'])
            message['To'] = '{0} <{1}>'.format(contact_obj.name, msg.urn_path)

            part1 = MIMEText(text, 'plain')
            message.attach(part1)

            server = transport(smtp_hostname, channel.config['EMAIL_SMTP_PORT'])
            server.login(channel.config['EMAIL_USERNAME'], channel.config['EMAIL_PASSWORD'])
            server.sendmail(channel.config['EMAIL_FROM'], msg.urn_path, message.as_string())
