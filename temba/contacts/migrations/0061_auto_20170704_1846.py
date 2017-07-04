# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-07-04 18:46
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0060_auto_20170512_2001'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contacturn',
            name='urn',
            field=models.CharField(choices=[('tel', 'Phone number'), ('facebook', 'Facebook identifier'), ('twitter', 'Twitter handle'), ('viber', 'Viber identifier'), ('line', 'LINE identifier'), ('telegram', 'Telegram identifier'), ('mailto', 'Email address'), ('ext', 'External identifier'), ('jiochat', 'Jiochat identifier'), ('fcm', 'Firebase Cloud Messaging identifier'), ('gcm', 'GCM identifier')], help_text='The Universal Resource Name as a string. ex: tel:+250788383383', max_length=255),
        ),
    ]
