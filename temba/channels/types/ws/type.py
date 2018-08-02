# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import requests
import time
import json
import six

from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import WS_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent
from temba.channels.models import TEMBA_HEADERS
from .views import ClaimView
from ...models import Channel, ChannelType, SendException


class WsType(ChannelType):
    """
    A WebSite channel
    """
    code = 'WS'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "WebSite"
    icon = 'icon-channel-external'
    show_config_page = True

    claim_blurb = _("""Connect to your website.""")
    claim_view = ClaimView

    schemes = [WS_SCHEME]
    attachment_support = False
    free_sending = True

    def send(self, channel, msg, text):
        data = {
            'id': str(msg.id),
            'text': text,
            'to': msg.urn_path,
            'to_no_plus': msg.urn_path.lstrip('+'),
            'from': channel.address,
            'from_no_plus': channel.address.lstrip('+'),
            'channel': str(channel.id)
        }

        metadata = msg.metadata if hasattr(msg, 'metadata') else {}
        quick_replies = metadata.get('quick_replies', [])

        formatted_replies = [dict(title=item) for item in quick_replies]

        url_buttons = metadata.get('url_buttons', [])
        if not quick_replies and url_buttons:
            formatted_replies = [dict(title=item.get('title'), url=item.get('url')) for item in url_buttons]

        if quick_replies:
            data['metadata'] = dict(quick_replies=formatted_replies)
        elif url_buttons:
            data['metadata'] = dict(url_buttons=formatted_replies)

        url = settings.WEBSOCKET_ADDRESS
        start = time.time()

        headers = {'Content-Type': 'application/json'}
        headers.update(TEMBA_HEADERS)

        payload = json.dumps(data)
        event = HttpEvent('POST', url, payload)

        try:
            response = requests.post(url, data=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from WebSocket Server" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
