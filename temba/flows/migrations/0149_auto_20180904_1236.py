# -*- coding: utf-8 -*-0168_auto_20180615_2040
# Generated by Django 1.11.6 on 2018-09-04 12:36
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0148_change_quick_replies_format'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flow',
            name='ignore_triggers',
            field=models.BooleanField(default=False, help_text='Ignore keyword and NLU triggers while in this flow'),
        ),
    ]
