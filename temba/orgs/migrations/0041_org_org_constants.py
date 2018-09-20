# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-09-20 14:03
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0040_org_nlu'),
    ]

    operations = [
        migrations.AddField(
            model_name='org',
            name='org_constants',
            field=models.TextField(default=dict, help_text='Organization Constants', null=True, verbose_name='Organization Constants'),
        ),
    ]
