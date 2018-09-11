# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import EMAIL_SCHEME
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
        pass
