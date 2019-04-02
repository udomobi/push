# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from temba.contacts.models import Contact


class Command(BaseCommand):  # pragma: no cover
    help = ""

    def handle(self, *args, **options):
        user = User.objects.get(username="AnonymousUser")
        orgs = [
            10,
            20,
            18,
            152,
            60,
            236,
            64,
            151,
            124,
            139,
            108,
            165,
            200,
            56,
            63,
            22,
            79,
            73,
            65,
            77,
            138,
            34,
            128,
            143,
            172,
            168,
            171,
            78,
            180,
            44,
            192,
            198,
            89,
            127,
            118,
            145,
            164,
            87,
            50,
            55,
            51,
            166,
            159,
            209,
            111,
            114,
            9,
            206,
            175,
            146,
            48,
            230,
            103,
            176,
            154,
            72,
            167,
            201,
            4,
            199,
            47,
            93,
            156,
            181,
            49,
            119,
            197,
            148,
            109,
            227,
            182,
            92,
            235,
            121,
            105,
            76,
            190,
            173,
            42,
            170,
            150,
            141,
            149,
            142,
            41,
            90,
            61,
            53,
            84,
            144,
            231,
            82,
            147,
            99,
            112,
            12,
            157,
            140,
            178,
            184,
            25,
            208,
            39,
        ]

        contacts = Contact.objects.filter(org_id__in=orgs)
        print("TOTAL: {}".format(contacts.count()))
        for contact in contacts:
            print(
                " ".join(
                    "ORG: ID {} {} - CONTACT: ID {} {}".format(
                        contact.org.name, contact.org.id, contact.id, contact.name
                    )
                )
                .encode("utf-8")
                .strip()
            )
            contact.release(user)
