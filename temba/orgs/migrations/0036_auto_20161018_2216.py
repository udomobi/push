# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0035_merge'),
    ]

    operations = [
        migrations.AlterField(
            model_name='org',
            name='brand',
            field=models.CharField(default=b'localhost:8000', help_text='The brand used in emails', max_length=128, verbose_name='Brand'),
        ),
    ]
