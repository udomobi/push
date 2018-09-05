# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import requests
import json
import re
import iso639

from django.conf import settings


class BothubConsumer(object):
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
        response = requests.request(
            method=method,
            url="{}/{}/".format(self.BASE_URL, url),
            headers={"Authorization": "{} {}".format(self.AUTH_PREFIX, self.bothub_authorization_key)},
            data=payload,
        )
        return response

    def predict(self, text, language=None):
        payload = {"text": str(text)}
        if language:
            try:
                lang = iso639.languages.get(part3=language)
                language = lang.part1
            except KeyError:
                pass
            payload.update({"language": language})
        response = self.request("parse", method="POST", payload=payload)

        if response.status_code != 200:
            return None, 0, None

        predict = json.loads(response.content)
        print(predict)
        intent = predict.get("intent", {})
        return intent.get("name", None), intent.get("confidence", 0), predict.get("entities", None)

    def is_valid_token(self):
        response = self.request("info")
        return True if response.status_code == 200 else False

    def get_repository_info(self):
        response = self.request("info")
        return json.loads(response.content)

    def get_intents(self):
        response = self.get_repository_info()
        return response.get("intents", {})
