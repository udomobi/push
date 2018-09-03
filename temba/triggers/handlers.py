# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from temba.msgs.handler import MessageHandler
from .models import Trigger


class TriggerHandler(MessageHandler):
    def __init__(self):
        super(TriggerHandler, self).__init__('triggers')

    def handle(self, msg):
        return Trigger.find_and_handle(msg)


class CatchAllHandler(MessageHandler):
    def __init__(self):
        super(CatchAllHandler, self).__init__('triggers')

    def handle(self, msg):
        return Trigger.catch_triggers(msg, Trigger.TYPE_CATCH_ALL, msg.channel)


class TriggerNluHandler(MessageHandler):

    def __init__(self):
        super(TriggerNluHandler, self).__init__("triggers nlu")

    def handle(self, msg):
        return Trigger.nlu_find_and_handle(msg)
