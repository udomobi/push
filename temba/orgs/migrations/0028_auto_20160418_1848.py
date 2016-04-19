# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0027_auto_20160418_1820'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='orderpayment',
            name='description',
        ),
        migrations.AddField(
            model_name='orderpayment',
            name='plan',
            field=models.CharField(default='basic', max_length=255, verbose_name='Plan'),
            preserve_default=False,
        ),
    ]
