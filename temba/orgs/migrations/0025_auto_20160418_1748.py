# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0024_auto_20160413_1432'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='orderpayment',
            name='plan',
        ),
        migrations.RemoveField(
            model_name='orderpayment',
            name='signature',
        ),
        migrations.AddField(
            model_name='orderpayment',
            name='description',
            field=models.CharField(max_length=255, null=True, verbose_name='Description plan'),
        ),
    ]
