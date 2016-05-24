# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0031_auto_20160422_2017'),
    ]

    operations = [
        migrations.AlterField(
            model_name='org',
            name='brand',
            field=models.CharField(default='rapidpro.io', help_text='The brand used in emails', max_length=128, verbose_name='Brand'),
        ),
    ]
