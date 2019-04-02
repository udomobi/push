# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import requests
import json
import re
import iso639

from django.conf import settings
from django.utils.translation import ugettext_lazy as _


class BotHubException(Exception):
    pass


class BotHubConsumer(object):
    """
    Bothub consumer
    This consumer will call Bothub api.
    """

    BASE_URL = settings.BOTHUB_BASE_URL
    AUTH_PREFIX = "Bearer"

    def __init__(self, authorization_key):
        if authorization_key.startswith("Bearer"):
            authorization_key = re.sub("^Bearer", "", authorization_key).strip()
        self.bothub_authorization_key = authorization_key

    def __str__(self):  # pragma: needs cover
        return self.bothub_authorization_key

    def get_authorization_key(self):
        return self.bothub_authorization_key

    def request(self, url, method="GET", payload=None):
        try:
            session = requests.Session()
            prepped = requests.Request(
                method=method,
                url="{}/{}/".format(self.BASE_URL, url),
                headers={"Authorization": "{} {}".format(self.AUTH_PREFIX, self.bothub_authorization_key)},
                data=payload,
            ).prepare()
            return session.send(prepped, timeout=settings.BOTHUB_TIMEOUT)
        except requests.RequestException:
            raise BotHubException(_("Bothub has offline. Try again."))

    def predict(self, text, language=None):
        payload = {"text": text.encode("utf-8")}
        if language:
            try:
                lang = iso639.languages.get(part3=language)
                language = lang.part1
            except KeyError:
                pass
            payload.update({"language": language})
        response = self.request("parse", method="POST", payload=payload)

        if response and response.status_code != 200:
            return None, 0, None

        predict = json.loads(response.content)
        intent = predict.get("intent", {})
        return intent.get("name", None), intent.get("confidence", 0), predict.get("entities", None)

    def is_valid_token(self):
        response = self.request("info")
        return True if response and response.status_code == 200 else False

    def get_repository_info(self):
        response = self.request("info")
        return json.loads(response.content)

    def get_intents(self):
        response = self.get_repository_info()
        return response.get("intents", {})
